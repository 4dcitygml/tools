#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The hub's review screen: a city's open proposals as CI judged them, for reviewers (S13: split
out of hub/app.py, code moved unchanged).

The module functions read GitHub's answers (PRs, commits, check runs, CI's report) into the
screen's rows; `Reviews` is the hub's review side, the base of `Hub`, using its clone (`root`),
its session (`_session`), its attribute names (`_attribute_labels`) and its editor model
(`_attr_repo`, `_attr_repo_lock`).
"""
from __future__ import annotations

import functools
import json
import mmap
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urlencode

import runtime
import building_identity
import citygml_dialect
import operator_explanation
import pr_classification
import pr_markers
import pr_reason

tr = runtime.translator("hub")   # server-generated text of the hub in the display language


def review_kind(pr: dict) -> str:
    """The PR's class for the review screen — Exchange Contract A5, decided by the shared table.

    `other` is a class like any other: it changes how the item is rendered, never
    whether it is shown (A5: review clients show every open PR)."""
    explicit = str(pr.get("review_kind") or "")
    if explicit in ("attribute", "texture", "geometry"):
        return explicit
    title = str(pr.get("title") or "")
    head = str((pr.get("head") or {}).get("ref") or pr.get("headRefName") or "")
    return pr_classification.classify_by_name(head, title) or "other"


def building_id_from_commit_messages(messages: "list[str]") -> str:
    """The building declared by the A2 trailers (the contract's canonical identity), or ''."""
    for message in messages:
        # the commit scope gate's parser (building_identity, git's trailer rule); the first building trailer
        found = building_identity.trailer_buildings(building_identity.trailers(str(message or "")))
        if found:
            return found[0]
    return ""


def github_list(token: str, path: str, key: str = "", *, strict: bool = False) -> list:
    """Every item of a GitHub listing, all pages, read by the contract's one paged reader
    (operator_explanation.pages). The check-runs and workflow-runs listings answer with an
    object; `key` names its list. A page GitHub refuses leaves the listing unread: strict
    raises GitHub's message, otherwise the listing is empty (fail-soft)."""
    def page(url):
        code, data = runtime.github_api(url, token)
        if code != 200:
            raise RuntimeError(runtime.api_error(code, data))
        return code, data
    try:
        return operator_explanation.pages(page, path, key or None)
    except RuntimeError:
        if strict:
            raise
        return []


def behind_base(token: str, nwo: str, base_ref: str, head_sha: str) -> bool:
    """Whether the base branch has commits the PR's head lacks, from GitHub's compare of the
    branch as it is now (a PR's base.sha is the base it was last updated on). GitHub's
    mergeable_state says "behind" only under the ruleset's up-to-date rule, which the cities
    do not use. Fail-soft: an unknown answer is not behind."""
    if not base_ref or not head_sha:
        return False
    code, data = runtime.github_api(
        f"/repos/{nwo}/compare/{quote(base_ref, safe='/')}...{head_sha}?per_page=1", token)
    behind = data.get("behind_by") if code == 200 and isinstance(data, dict) else None
    return isinstance(behind, int) and behind > 0


def pr_behind(token: str, nwo: str, pr: dict) -> bool:
    return behind_base(token, nwo, str((pr.get("base") or {}).get("ref") or ""),
                       str((pr.get("head") or {}).get("sha") or ""))


class PrRecords:
    """One PR's GitHub records, each listing read once on the paged reader when first asked
    (V5: the queue and the detail spelled their reads again, each its own way). CI's comments
    are the Actions bot's only (D-3a4)."""

    def __init__(self, token: str, nwo: str, pr: dict):
        self.token, self.nwo, self.pr = token, nwo, pr
        self.number = int(pr.get("number") or 0)
        self.head = str((pr.get("head") or {}).get("sha") or "")

    def _pull(self, part: str) -> list:
        return github_list(self.token, f"/repos/{self.nwo}/pulls/{self.number}/{part}") if self.number else []

    @functools.cached_property
    def commits(self) -> list:
        return self._pull("commits")

    @functools.cached_property
    def files(self) -> list:
        return self._pull("files")

    @functools.cached_property
    def reviews(self) -> list:
        return self._pull("reviews")

    @functools.cached_property
    def comments(self) -> list:
        return [c for c in github_list(self.token, f"/repos/{self.nwo}/issues/{self.number}/comments")
                if operator_explanation.bot(c)] if self.number else []

    @functools.cached_property
    def check_runs(self) -> list:
        return github_list(self.token, f"/repos/{self.nwo}/commits/{self.head}/check-runs",
                           "check_runs") if self.head else []


def latest_reviews(reviews: list) -> dict:
    """One current opinion per account: a later APPROVED / CHANGES_REQUESTED / DISMISSED
    review replaces the earlier one; plain comments neither add nor erase a vote."""
    latest: dict = {}
    for index, review in enumerate(reviews):
        if review.get("state") not in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            continue
        # GitHub always names the reviewer; a record without one cannot be merged with another.
        login = str((review.get("user") or {}).get("login") or "").lower() or f"#{review.get('id') or index}"
        if int(review.get("id") or 0) >= int(latest.get(login, {}).get("id") or 0):
            latest[login] = review
    return latest


def extract_building_id(pattern: "str | None", *texts: object) -> str:
    """A building ID in the title, body, branch name, etc., by the form the city's dialect pack
    gives its IDs in free text (Pack.building_id_text); "" when it has none."""
    for text in texts if pattern else ():
        hit = re.search(pattern, str(text or ""))
        if hit:
            return hit.group(1)
    return ""


def attr_editor_module():
    """The attribute editor, bundled next to the hub (imported on first use: the review
    screen borrows its attribute names and its 3D model of a building)."""
    from attr_editor import app
    return app


def attribute_labels() -> dict[str, str]:
    """Display names of attributes: the attribute editor's dictionary, the one owner (V5: the hub
    kept twelve of them again, word for word)."""
    try:
        return {str(k): str(v) for k, v in attr_editor_module().LABELS.items()}
    except Exception:   # a broken editor module must not stop the hub
        return {}


def attribute_local_name(path: str) -> str:
    """Get the trailing attribute name from a path like `/bldg:x/bldg:y[1]`."""
    raw = str(path or "").strip().strip("`")
    tail = raw.rstrip("/").rsplit("/", 1)[-1]
    tail = re.sub(r"\[.*?\]", "", tail)
    return tail.rsplit(":", 1)[-1]


_PATH_LIKE = re.compile(r"^[/A-Za-z0-9_:.\[\]-]+$")


def attribute_label(path: str, labels: "dict[str, str] | None" = None) -> str:
    """The display name of a table item: an attribute path or tag (the CI change
    summary) becomes its label; a display name the editor already wrote in the
    repository language is kept as it is."""
    raw = str(path or "").strip().strip("`")
    if raw and not _PATH_LIKE.match(raw):
        return raw
    local = attribute_local_name(raw)
    known = (labels or attribute_labels()).get(local)
    if known:
        return known
    return f"Other attribute ({local or 'unknown item'})"


def _replace_attribute_terms(text: str, labels: "dict[str, str] | None" = None) -> str:
    out = str(text or "")
    for key, label in sorted((labels or attribute_labels()).items(), key=lambda x: len(x[0]), reverse=True):
        out = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", label, out)
    return out


def human_proposal_title(title: str, labels: "dict[str, str] | None" = None) -> str:
    """Format the proposal title as a building-ledger heading."""
    value = str(title or tr("hub.proposal_title_fallback", "Change record"))
    value = _replace_attribute_terms(value, labels)
    value = value.strip()
    # Capitalize the first letter after stripping validation prefixes (English titles
    # arrive lowercase mid-sentence; no effect on Japanese titles).
    if value and value[0].islower():
        value = value[0].upper() + value[1:]
    return value or tr("hub.proposal_title_fallback", "Change record")


def human_reason(
    body: str, kind: str = "other", labels: "dict[str, str] | None" = None
) -> str:
    """Extract from the PR body only the explanation staff need for their decision."""
    # the section CI reads (pr_reason); a short attribute / geometry proposal without one is shown
    # whole. Shown only: whether it is filled is judged as CI judges it (_pr_state, D-3a7)
    reason = pr_reason.section(body) or (body if kind in ("attribute", "geometry") else "")
    reason = re.sub(r"<!--.*?-->", "", reason, flags=re.DOTALL)
    useful: list[str] = []
    in_fence = False
    for line in reason.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or stripped.startswith("|") or re.match(r"^- \[[ xX]\]", stripped):
            continue
        if stripped.startswith("#"):
            stripped = re.sub(r"^#+\s*", "", stripped)
        if stripped in body_headings():
            continue
        useful.append(stripped)
    result = "\n".join(useful).strip()
    result = re.sub(r"\*\*(.*?)\*\*|__(.*?)__", lambda m: m.group(1) or m.group(2), result)
    result = result.replace("`", "")
    result = _replace_attribute_terms(result, labels)
    return result.strip()


# The PR body is written by the editors in the repository language (their catalogs)
# or by a person from the city template (English). The hub reads it back with the
# same words, so no language has its own list here.
_CATALOG_APPS = ("attr_editor", "tex_editor")
_TEMPLATE_HEADINGS = ("PR type", "Target buildings / scope", "Summary of changes", "Change type",
                      "Image rights", "Checklist", "Related issues", "Additional notes (optional)")
_catalog_cache: dict[str, dict] = {}


def _catalog_values(prefix: str) -> dict[str, set[str]]:
    """Catalog values by key, over both editors and every language, for keys starting with prefix."""
    if prefix not in _catalog_cache:
        found: dict[str, set[str]] = {}
        for app in _CATALOG_APPS:
            for lang in runtime.i18n_loader.SUPPORTED:
                path = runtime.i18n_loader.CATALOG_DIR / app / f"{lang}.json"
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                for key, value in data.items():
                    if key.startswith(prefix) and isinstance(value, str) and value.strip():
                        found.setdefault(key, set()).add(value.strip())
        _catalog_cache[prefix] = found
    return _catalog_cache[prefix]


def change_table_words() -> dict[str, set[str]]:
    """Header cells of the before/after tables: label | before | after [| source]
    (or before | after for textures) as the editors write them, and path | Old | New
    as the CI change summary writes them."""
    words = {"label": {"path"}, "before": {"Old"}, "after": {"New"}}
    columns = _catalog_values("pr.details_columns") | _catalog_values("pr.tex_columns")
    for rows in columns.values():
        for row in rows:
            cells = [c.strip() for c in row.strip().strip("|").split("|") if c.strip()]
            if len(cells) >= 3:
                words["label"].add(cells[0]); words["before"].add(cells[1]); words["after"].add(cells[2])
            elif len(cells) == 2:
                words["before"].add(cells[0]); words["after"].add(cells[1])
    return words


def no_texture_placeholders() -> set[str]:
    """What the texture editor writes for "no image before" (a new texturing), in every language
    (D-3a3: only the Japanese one was recognised, so an English PR showed a broken image)."""
    return _catalog_values("pr.tex_no_existing").get("pr.tex_no_existing", set())


def body_headings() -> set[str]:
    """Every section heading an editor or the city template writes (dropped from a reason
    that had to fall back to the whole body)."""
    return set().union(*_catalog_values("pr.heading_").values()) | set(_TEMPLATE_HEADINGS)


def evidence_links(markdown: str) -> list[dict]:
    """Split links in the evidence section into human-facing names and URLs."""
    links: list[dict] = []
    seen: set[str] = set()
    for label, url in re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", markdown or ""):
        if url not in seen:
            links.append({"label": label.strip() or tr("hub.evidence_link", "Supporting document"),
                          "url": url})
            seen.add(url)
    without_markdown = re.sub(r"\[[^\]]+\]\(https?://[^)\s]+\)", "", markdown or "")
    for url in re.findall(r"https?://[^\s<>)]+", without_markdown):
        url = url.rstrip(".,、。")
        if url not in seen:
            links.append({"label": tr("hub.evidence_link", "Supporting document"), "url": url})
            seen.add(url)
    return links


def check_display_name(name: str) -> str:
    """Convert internal CI/check-run names to display names (selected language) that convey what is judged."""
    key = str(name or "").strip().lower()
    rules = (
        (("preview", "cesium", "3d"),
         tr("hub.check_preview", "3D view necessity and generation check")),
        (("texture", "appearance"),
         tr("hub.check_texture", "Facade image and building surface consistency check")),
        (("reviewability", "lint"),
         tr("hub.check_reviewability", "Change readability check")),
        (("metadata", "history", "lifecycle"),
         tr("hub.check_metadata", "Change history record check")),
        (("citygml", "validation", "validate", "schema"),
         tr("hub.check_citygml", "CityGML format check")),
        (("analyze", "analysis", "scope"),
         tr("hub.check_analyze", "Change scope and data format check")),
    )
    for needles, label in rules:
        if any(n in key for n in needles):
            return label
    return tr("hub.check_generic", "Automated building data check")


ROW_STATUSES = ("pass", "warn", "fail", "error", "na", "pending")   # Exchange Contract A6


def ci_verdict(check_runs: list, pr: dict) -> dict:
    """CI's verdict on the PR, read once (A10): the trusted `ci-report` check run and the report
    it carries, while that report describes the PR as GitHub holds it; `analyze` says whether an
    analysis still runs. -> status (pass / fail / pending), the report's state (pass / fix /
    system; '' without a current report), its A6 rows (key -> status) and the trusted side's
    reason."""
    def latest(name: str) -> "dict | None":
        runs = [c for c in check_runs
                if c.get("name") == name and (c.get("app") or {}).get("slug") == "github-actions"]
        return max(runs, key=lambda c: int(c.get("id") or 0)) if runs else None
    analyze, check = latest("analyze"), latest("ci-report")
    reason, report = operator_explanation.read_check(check)
    current = bool(report) and report.get("pr") == pr.get("number") \
        and report.get("context") == operator_explanation.context(pr)
    if not current or not analyze or analyze.get("status") != "completed" or check.get("status") != "completed":
        return {"status": "pending", "state": "", "rows": {}, "reason": reason or "report-pending"}
    state = str(report.get("state") or "")
    rows = {r["key"]: r["status"] for r in report.get("checks") or []
            if isinstance(r, dict) and isinstance(r.get("key"), str) and r.get("status") in ROW_STATUSES}
    if reason == "current":
        status = "pass" if state == "pass" else "fail"
    else:   # workflow-change and duplicate-head wait for a person; anything else for CI
        status = "fail" if reason in ("workflow-change", "duplicate-head") else "pending"
    return {"status": status, "state": state, "rows": rows, "reason": reason}


def graphql_pr(node: dict) -> dict:
    """A GraphQL PullRequest in the REST shape operator_explanation.context reads."""
    return {"number": node.get("number"), "title": node.get("title"), "body": node.get("body"),
            "head": {"sha": node.get("headRefOid") or ""}, "base": {"sha": node.get("baseRefOid") or ""},
            "labels": [{"name": x.get("name")} for x in (node.get("labels") or {}).get("nodes") or [] if x]}


def graphql_check_runs(node: dict) -> list:
    """The `ci-report` and `analyze` check runs of a GraphQL PullRequest's head, REST-shaped."""
    commits = ((node.get("commits") or {}).get("nodes") or [])
    suites = ((((commits[-1] or {}).get("commit") or {}).get("checkSuites") or {}).get("nodes") or []) if commits else []
    return [{"id": run.get("databaseId"), "name": run.get("name"), "app": suite.get("app") or {},
             "status": str(run.get("status") or "").lower(), "conclusion": str(run.get("conclusion") or "").lower(),
             "output": {"text": run.get("text") or ""}}
            for suite in suites if suite for kind in ("report", "analysis")
            for run in ((suite.get(kind) or {}).get("nodes") or []) if run]


def ci_retry_info(verdict: dict) -> dict:
    """Explain, per cause, whether re-inspection on the same data is useful, from CI's verdict
    (ci_verdict). Being behind the base is no cause: the analysis does not judge it (A6 v4.0.0)."""
    check_status = verdict.get("status", "pending")
    if verdict.get("state") == "fix":   # a finding in the data: the proposer acts
        return {
            "available": False, "kind": "fix",
            "label": tr("hub.retry_fix_label", "Fix the content and the checks re-run automatically"),
            "reason": tr("hub.retry_fix_reason",
                         "There are items to confirm in the data or the description. "
                         "Sending a fix re-runs the checks automatically"),
        }
    if check_status == "fail":
        return {
            "available": True, "kind": "system",
            "label": tr("hub.retry_system_label", "Re-run the automated checks"),
            "reason": tr("hub.retry_system_reason",
                         "No content issues were found; this may be a temporary "
                         "failure of the check process"),
        }
    if check_status == "pending":
        return {
            "available": False, "kind": "pending",
            "label": tr("hub.retry_pending_label", "Automated checks running"),
            "reason": tr("hub.retry_pending_reason", "Please wait until the checks finish"),
        }
    return {
        "available": False, "kind": "none", "label": "",
        "reason": tr("hub.retry_none_reason", "No re-run is needed"),
    }


def review_checkpoints(
    *, reason_ok: bool, unsafe_files: list, verdict: dict, model_available: bool,
    kind: str = "attribute", changes_requested: bool = False, adjustment_owner: str = "ci",
    behind: bool = False,
) -> list[dict]:
    """List every inspection item — pass, not-applicable, and fail — for the person in charge.

    A row's state is CI's: the current report's rows (ci_verdict, A6). Until CI has one, the
    hub's own reading below fills the rows it can see (the reason, the files, the 3D view) and
    the rest wait. Returned with `label` (display name in the selected language).
    """
    check_status = verdict.get("status", "pending")
    summary = dict(verdict.get("rows") or {})
    resubmit = tr("hub.cp_wait_proposer_ci",
                  "CI has posted items to confirm. Waiting for the proposer to respond")
    if changes_requested and adjustment_owner == "reviewer":
        resubmit = tr("hub.cp_wait_proposer_reviewer",
                      "The reviewer has sent a comment to confirm. "
                      "Waiting for the proposer to respond")

    def point(key: str, match: str, label: str, status: str, ok: str, ng: str, note: str = "") -> dict:
        # Matching prefers the key (<!--cp:key-->), falling back to the English display name
        status = summary.get(key, summary.get(match, status))
        reason = ok if status == "pass" else ng
        if status == "na":
            reason = tr("hub.cp_na_reason", "Not applicable to this change")
            action = tr("hub.cp_no_action", "No action is needed")
        elif status == "pending":
            reason = tr("hub.cp_pending_reason", "Automated checks are running")
            action = tr("hub.cp_pending_action",
                        "Automated checks handle this until completion. "
                        "No reviewer action is needed")
        elif status == "fail":
            action = resubmit
        elif status == "warn":
            action = note or tr("hub.cp_warn_action",
                                "An advisory finding: it does not block. Read it and decide in your review")
        elif status == "error":
            reason = tr("hub.cp_error_reason",
                        "The check could not reach a result: a system error, not a problem in the data")
            action = tr("hub.cp_error_action",
                        "CI or a maintainer repeats the check; request a re-inspection if it persists")
        else:
            action = tr("hub.cp_no_action", "No action is needed")
        return {"key": key, "label": label, "status": status, "reason": reason, "action": action}

    # Not a CI row (A6 v4.0.0): GitHub's own fact, the live compare of the base with the head
    # (pr_behind). A note, never a hold: GitHub's "Update branch" brings the base in. A report
    # from an analysis before tools v1.6 still has the row; GitHub's fact wins over it.
    summary.pop("freshness", None)
    freshness = "warn" if behind else "pass"
    commit_scope = reviewability = structure = code_list = plausibility = check_status
    topology = check_status if kind == "geometry" else "na"
    texture = check_status if kind == "texture" else "na"
    return [
        point("reason", "Description and evidence",
              tr("hub.cp_reason_label", "Description and evidence"),
              "pass" if reason_ok else "fail",
              tr("hub.cp_reason_ok",
                 "The reason for the change and the evidence can be confirmed"),
              tr("hub.cp_reason_ng",
                 "The reason for the change or the evidence is not filled in")),
        point("classification", "Change classification",
              tr("hub.cp_classification_label", "Change classification"),
              summary.get("classification", "na"),
              tr("hub.cp_classification_ok",
                 "The kind of change is declared (branch prefix or title)"),
              tr("hub.cp_classification_ng",
                 "The kind of change is not declared; CI posted how to fix it")),
        point("commit-scope", "One change = one building",
              tr("hub.cp_commit_scope_label", "One change = one building"), commit_scope,
              tr("hub.cp_commit_scope_ok", "The change is contained to one building"),
              tr("hub.cp_commit_scope_ng",
                 "The change touches multiple buildings, or the building IDs are inconsistent")),
        # the bulk rows (A7): not applicable unless CI's rows say otherwise
        point("scope-reproducibility", "Ward extraction reproducibility",
              tr("hub.cp_scope_reproducibility_label", "Ward extraction reproducibility"), "na",
              tr("hub.cp_scope_reproducibility_ok", "The ward extraction was reproduced from its source"),
              tr("hub.cp_scope_reproducibility_ng", "The ward extraction could not be reproduced from its source")),
        point("reproduction", "Bulk conversion reproduction",
              tr("hub.cp_reproduction_label", "Bulk conversion reproduction"), "na",
              tr("hub.cp_reproduction_ok", "The bulk conversion was reproduced from its manifest"),
              tr("hub.cp_reproduction_ng", "The bulk conversion could not be reproduced from its manifest")),
        point("freshness", "Consistency with the latest version",
              tr("hub.cp_freshness_label", "Consistency with the latest version"), freshness,
              tr("hub.cp_freshness_ok", "The change is based on the latest published data"),
              tr("hub.cp_freshness_ng",
                 "Another change was applied first, so the latest version needs to be merged in"),
              tr("hub.cp_freshness_note",
                 "It does not block. The proposer or a maintainer can bring it up to date with "
                 "GitHub's \"Update branch\" on the proposal")),
        point("file-scope", "Changed file scope",
              tr("hub.cp_file_scope_label", "Changed file scope"),
              "pass" if not unsafe_files else "fail",
              tr("hub.cp_file_scope_ok", "Only the target building data is changed"),
              tr("hub.cp_file_scope_ng", "Files other than building data are included")),
        point("schema", "CityGML format",
              tr("hub.cp_schema_label", "CityGML format"), check_status,
              tr("hub.cp_schema_ok", "The file conforms to the CityGML syntax and schema"),
              tr("hub.cp_schema_ng",
                 "There is an inconsistency in the CityGML syntax or schema")),
        point("minimal-diff", "Minimal diff",
              tr("hub.cp_minimal_diff_label", "Minimal diff"), reviewability,
              tr("hub.cp_minimal_diff_ok", "There is no large diff unnecessary for review"),
              tr("hub.cp_minimal_diff_ng",
                 "The change is large, or unnecessary ID changes are suspected")),
        point("texture", "Texture consistency",
              tr("hub.cp_texture_label", "Texture consistency"), texture,
              tr("hub.cp_texture_ok",
                 "The facade image references and update method are fine"),
              tr("hub.cp_texture_ng",
                 "There is a problem with the facade image references or update method")),
        point("structure", "Geometric structure",
              tr("hub.cp_structure_label", "Geometric structure"), structure,
              tr("hub.cp_structure_ok", "The surface and ring structures are fine"),
              tr("hub.cp_structure_ng",
                 "There is an inconsistency in the surface or ring structures")),
        point("code-list", "Code list conformance",
              tr("hub.cp_code_list_label", "Code list conformance"), code_list,
              tr("hub.cp_code_list_ok", "No changed code value is missing from its code list"),
              tr("hub.cp_code_list_ng", "A changed code value is not in its code list")),
        point("plausibility", "Attribute value plausibility",
              tr("hub.cp_plausibility_label", "Attribute value plausibility"), plausibility,
              tr("hub.cp_plausibility_ok", "The attribute values and code values are fine"),
              tr("hub.cp_plausibility_ng",
                 "There are attribute or code values that need fixing")),
        point("topology", "Topological consistency",
              tr("hub.cp_topology_label", "Topological consistency"), topology,
              tr("hub.cp_topology_ok",
                 "The change introduces no new topological inconsistencies"),
              tr("hub.cp_topology_ng",
                 "The change introduces new topological inconsistencies")),
        point("model", "3D view",
              tr("hub.cp_model_label", "3D view"), "pass" if model_available else "warn",
              tr("hub.cp_model_ok", "The building's 3D model can be viewed"),
              tr("hub.cp_model_ng", "The building's 3D model cannot be prepared")),
    ]


def markdown_tables(text: str) -> list[dict]:
    """Structure the simple Markdown tables in the PR body / CI comments."""
    tables: list[dict] = []
    lines = text.splitlines()
    i = 0
    while i + 1 < len(lines):
        head = lines[i].strip()
        sep = lines[i + 1].strip()
        if not (head.startswith("|") and sep.startswith("|") and "---" in sep):
            i += 1
            continue

        def cells(line: str) -> list[str]:
            return [c.strip().strip("`") for c in line.strip().strip("|").split("|")]

        headers = cells(head)
        rows: list[dict] = []
        i += 2
        while i < len(lines) and lines[i].strip().startswith("|"):
            vals = cells(lines[i])
            if len(vals) == len(headers):
                rows.append(dict(zip(headers, vals)))
            i += 1
        tables.append({"headers": headers, "rows": rows})
    return tables


# ---- Theme pack (shares tools/themes/theme_loader.py; runs without themes if missing) ----


# ---- Language pack (shares tools/i18n/i18n_loader.py; runs with the original text if missing) ----


def _find_whole(data, token: str) -> int:
    """First offset of token as a whole value in CityGML bytes: an attribute value or an
    element's text, so bounded by a quote or '>' before and a quote or '<' after. A plain
    find matched 13102-bldg-83 inside 13102-bldg-8357 and showed another building."""
    needle = token.encode("utf-8")
    if not needle:
        return -1
    pos = 0
    while True:
        hit = data.find(needle, pos)
        if hit < 0:
            return -1
        before, after = data[hit - 1:hit] if hit else b"", data[hit + len(needle):hit + len(needle) + 1]
        if before in (b">", b'"', b"'") and after in (b"<", b'"', b"'"):
            return hit
        pos = hit + 1


class Reviews:
    """The review side of a city's Hub (see the module docstring for what it uses of the Hub)."""

    # ---- PR review for administrators ----
    def _review_identity(self) -> tuple[str, str, str]:
        token, login = self._session.github_login()
        nwo = runtime.upstream_nwo(getattr(self, "root", None))
        if not nwo:
            raise RuntimeError(tr("hub.err_no_city", "This copy of the data does not name its city (4dcitygml.json repo). "
                               "Open the city's copy, or make a new one from the setup screen."))
        if not token or not login:
            raise RuntimeError(tr("hub.err_connect_first", "Connect to GitHub first"))
        return token, login, nwo

    def reviewer_permission(self) -> dict:
        token, login, nwo = self._review_identity()
        # GitHub's own answer for this token: the repository's `permissions` (V5: the hub asked
        # the collaborator endpoint and kept its own list of names that may approve)
        code, data = runtime.github_api(f"/repos/{nwo}", token)
        flags = (data.get("permissions") or {}) if code == 200 and isinstance(data, dict) else {}
        permission = next((name for name in ("admin", "maintain", "push", "triage", "pull") if flags.get(name)), "none")
        return {"login": login, "permission": permission, "canReview": bool(flags.get("push"))}

    # ---- First-time contributors: fork runs GitHub holds for the maintainer ----
    def pending_runs(self) -> dict:
        """Workflow runs of fork PRs from first-time contributors that wait in action_required
        until a maintainer approves them. Listed for an account that can approve, each linking
        to its PR on GitHub, where GitHub's own "Approve and run workflows" shows every changed
        file and warns about workflow changes (V3: the hub approved them itself, with its own
        guard, a re-implementation of that page)."""
        if not self.reviewer_permission()["canReview"]:
            return {"ok": True, "canReview": False, "runs": []}
        token, _, nwo = self._review_identity()
        held = github_list(token, f"/repos/{nwo}/actions/runs?status=action_required",
                           "workflow_runs", strict=True)
        pulls = {sha: pr for pr in github_list(token, f"/repos/{nwo}/pulls?state=open")
                 if (sha := (pr.get("head") or {}).get("sha") or "")}
        runs = [{"runId": run.get("id"), "workflow": run.get("name") or "", "number": pr.get("number"),
                 "title": pr.get("title") or "", "author": ((pr.get("user") or {}).get("login")) or "",
                 "created": run.get("created_at") or "", "url": pr.get("html_url") or ""}
                for run in held if (pr := pulls.get(str(run.get("head_sha") or ""))) is not None]
        return {"ok": True, "canReview": True, "runs": sorted(runs, key=lambda r: str(r["created"]))}

    def review_entry(self) -> dict:
        """Whether the dashboard shows the maintainer card: only to an account that can approve
        in this city. Fail-closed: not connected or GitHub unreachable hides it."""
        try:
            return {"ok": True, "canReview": self.reviewer_permission()["canReview"]}
        except Exception:
            return {"ok": True, "canReview": False}

    @staticmethod
    def _summary_gml_ids(comments: list) -> list[str]:
        """Get the gml:ids from the building headings of the change summary."""
        ids: list[str] = []
        for comment in comments:
            body = str(comment.get("body") or "")
            if pr_markers.CHANGE_SUMMARY not in body:
                continue
            for value in re.findall(r"^####\s+`([^`]+)`\s*$", body, flags=re.MULTILINE):
                if value not in ids:
                    ids.append(value)
        return ids

    def _building_ref_from_local_files(
        self, files: list, comments: list, stable_id: str = ""
    ) -> dict:
        """Match the change target against local CityGML and return the ID, position, and display references."""
        gml_ids = self._summary_gml_ids(comments)
        building_start = re.compile(rb"<(?:\w+:)?Building\b")
        gml_id_attr = re.compile(rb"\bgml:id=[\"']([^\"']+)[\"']")
        # the city's own identity rule (4dcitygml.json building_id): one profile's ID leaf was
        # hard-coded here, so a gml:id or gen: city got another ID than the gates give (S11)
        rule = building_identity.rule_from_config(runtime.city_meta(self.root))
        pos_list_tag = re.compile(
            rb"<(?:\w+:)?posList\b[^>]*>([^<]+)</(?:\w+:)?posList>"
        )
        for file in files:
            rel = str(file.get("filename") or file.get("path") or "")
            if not self._is_data_file(rel, images=False):
                continue
            source = self.root / rel
            try:
                with source.open("rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as data:
                    candidates: list[tuple[str, int]] = []
                    for gml_id in gml_ids:
                        hit = _find_whole(data, gml_id)
                        if hit >= 0:
                            candidates.append((gml_id, hit))
                    if stable_id and not candidates:
                        hit = _find_whole(data, stable_id)
                        if hit < 0:
                            continue
                        start_match = None
                        for match in building_start.finditer(data, 0, hit):
                            start_match = match
                        if start_match:
                            # up to the quote after the hit: in a gml:id city the stable ID is the gml:id itself
                            id_match = gml_id_attr.search(data, start_match.start(), hit + len(stable_id.encode()) + 1)
                            if id_match:
                                candidates.append((
                                    id_match.group(1).decode("utf-8", errors="replace"),
                                    start_match.start(),
                                ))
                    for gml_id, hit in candidates:
                        next_building = building_start.search(data, hit + len(gml_id))
                        end = next_building.start() if next_building else min(len(data), hit + 8_000_000)
                        resolved_id = building_identity.stable_id(data[hit:end], gml_id, rule)
                        center = None
                        pos_match = pos_list_tag.search(data, hit, end)
                        if pos_match:
                            try:
                                nums = [float(x) for x in pos_match.group(1).split()]
                                # in the file's own CRS: a projected one (UTM, feet) is converted
                                srs = re.search(rb'srsName="([^"]*)"', data[:8192])
                                tf = citygml_dialect.crs_transformer(
                                    (srs.group(1).decode("utf-8", "replace") if srs else "") or (runtime.city(self.root).crs or ""))
                                # an unknown CRS gives no center (no map), never lat/lon
                                points = [tf(x, y) for x, y in zip(nums[0::3], nums[1::3])] if tf else []
                                lats = [p[0] for p in points]
                                lons = [p[1] for p in points]
                                if lats and lons:
                                    center = [
                                        round((min(lats) + max(lats)) / 2, 7),
                                        round((min(lons) + max(lons)) / 2, 7),
                                    ]
                            except ValueError:
                                center = None
                        return {
                            "buildingId": resolved_id,
                            "gid": gml_id,
                            "tile": next((code for code, p in runtime.tile_files(source.parent, runtime.city(self.root).pack).items()
                                          if p.name == source.name), source.stem),   # the editors' tile code
                            "center": center,
                        }
            except (OSError, ValueError):
                continue
        return {}

    def _building_id_from_local_files(self, files: list, comments: list) -> str:
        """Match the summary's gml:id against local CityGML and convert it to a stable building ID."""
        return str(self._building_ref_from_local_files(files, comments).get("buildingId") or "")

    def _attribute_repo(self):
        """The attribute editor's model of the clone (the 3D view of the review screen), built once."""
        if self._attr_repo is None:
            with self._attr_repo_lock:
                if self._attr_repo is None:
                    self._attr_repo = attr_editor_module().Repo(self.root)
        return self._attr_repo

    def _is_data_file(self, rel: str, gml: bool = True, images: bool = True) -> bool:
        """Whether a repository path is city data: a .gml or texture image inside the city's data
        directories (4dcitygml.json data_dirs)."""
        if not rel or rel.startswith("/") or ".." in Path(rel).parts:
            return False
        kinds = ((r"\.gml",) if gml else ()) + ((r"\.(?:jpe?g|png|tiff?)",) if images else ())
        if not kinds or not re.search(r"(?:" + "|".join(kinds) + r")$", rel, re.IGNORECASE):
            return False
        root = Path(self.root).resolve()
        return any(rel.startswith(d.relative_to(root).as_posix() + "/") for d in runtime.data_dirs(root))

    def review_building_model(self, tile: str, gid: str) -> dict:
        if tile not in self._attribute_repo().tile_files():
            raise ValueError(tr("hub.err_bad_tile", "Unknown tile"))
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", gid):
            raise ValueError(tr("hub.err_bad_building_id", "Invalid building identifier"))
        return self._attribute_repo().building_json(tile, gid)

    def review_texture(self, path: str) -> tuple[bytes, str]:
        """Return only the local appearance images used by the 3D view."""
        repo = self._attribute_repo()
        source = (repo.bldg_dir / path).resolve()
        if (".." in Path(path).parts or not source.is_relative_to(repo.bldg_dir.resolve())
                or not re.search(r"\.(?:jpe?g|png|tiff?)$", path, re.IGNORECASE)):
            raise ValueError(tr("hub.err_not_appearance", "Only facade images can be fetched"))
        if not source.is_file():
            raise FileNotFoundError(tr("hub.err_appearance_missing", "Facade image not found"))
        return source.read_bytes(), runtime.media_type(source)

    def _pr_building_id(self, rec: PrRecords) -> str:
        """The building a PR is about, by one chain (V5; it was spelled three times): the A2
        trailers of its commits (tool-independent), then the title, body, branch and CI's
        comments, then the local files CI's change summary names."""
        pr = rec.pr
        return (building_id_from_commit_messages([str((c.get("commit") or {}).get("message") or "")
                                                  for c in rec.commits])
                or extract_building_id(runtime.city(self.root).pack.building_id_text,
                                       pr.get("title"), pr.get("body"), (pr.get("head") or {}).get("ref"),
                                       *[c.get("body") for c in rec.comments])
                or self._building_id_from_local_files(rec.files, rec.comments))

    @staticmethod
    def _pr_state(rec: PrRecords) -> dict:
        """What the queue and the detail both judge, once (V5): CI's verdict; whether the reason
        is filled (CI's row once it is there, before that pr_reason, as CI will judge: D-3a7, the
        preview fell back to the whole body); whether the reviewer asked for changes on this head."""
        pr = rec.pr
        verdict = ci_verdict(rec.check_runs, pr)
        reason_ok = (verdict["rows"]["reason"] == "pass" if "reason" in verdict["rows"]
                     else pr_reason.acceptable(str(pr.get("body") or "")))
        reviewer_feedback = any(r.get("state") == "CHANGES_REQUESTED" and str(r.get("commit_id") or "") == rec.head
                                for r in latest_reviews(rec.reviews).values())
        return {"verdict": verdict, "reasonOk": reason_ok, "reviewerFeedback": reviewer_feedback}

    def _awaiting_approval(self, token: str, nwo: str) -> frozenset:
        """Head commits whose runs GitHub holds for a maintainer's approval (first-time
        contributors' fork PRs). They have no check runs yet; fail-soft: empty on error."""
        runs = github_list(token, f"/repos/{nwo}/actions/runs?status=action_required", "workflow_runs")
        return frozenset(str(r.get("head_sha") or "") for r in runs)

    def _review_queue_item(self, token: str, nwo: str, pr: dict,
                           awaiting_approval: frozenset = frozenset()) -> "dict | None":
        """Build one list item, with two states based on who works on it next.

        Every open PR against main is listed whatever its class (A5); `other`
        and unclassified proposals are shown as plain items."""
        kind = review_kind(pr)
        if str((pr.get("base") or {}).get("ref") or "") != "main":
            return None
        number = int(pr.get("number") or 0)
        rec = PrRecords(token, nwo, pr)
        state = self._pr_state(rec)
        verdict, reason_ok, reviewer_feedback = state["verdict"], state["reasonOk"], state["reviewerFeedback"]
        head_sha = rec.head
        check_status = verdict["status"]
        # GitHub holds this head's analysis until a maintainer allows the first-time run: the
        # checks it has (a skipped gatekeeper) say nothing yet
        needs_approval = head_sha in awaiting_approval
        if needs_approval:
            check_status = "pending"

        behind = pr_behind(token, nwo, pr)
        auto_resubmit = verdict["state"] == "fix" or not reason_ok or check_status == "fail"
        adjustment_reasons = []
        if not reason_ok:
            adjustment_reasons.append(
                tr("hub.adjust_reason_missing", "Reason and evidence not filled in"))
        if check_status == "fail":
            adjustment_reasons.append(
                tr("hub.adjust_checks_failing", "Automated checks have failing items"))
        elif needs_approval:
            adjustment_reasons.append(tr(
                "hub.adjust_needs_approval",
                "A first-time contributor: the checks run once a maintainer allows them"))
        elif check_status == "pending":
            adjustment_reasons.append(tr("hub.retry_pending_label", "Automated checks running"))
        elif auto_resubmit and reason_ok:
            adjustment_reasons.append(tr("hub.adjust_ci_items", "CI has items to confirm"))
        if reviewer_feedback:
            adjustment_reasons.append(
                tr("hub.adjust_reviewer_comment", "The reviewer has posted a comment to confirm"))
        if pr.get("draft"):
            adjustment_reasons.append(tr("hub.adjust_draft", "The proposer is still drafting"))
        ready = (
            reason_ok and check_status == "pass" and not pr.get("draft")
            and not reviewer_feedback and not auto_resubmit
            and str(pr.get("state") or "").lower() == "open"
        )
        queue_status = "reviewer_waiting" if ready else "proposer_waiting"
        if ready:
            waiting_source = ""
        elif reviewer_feedback:
            waiting_source = "reviewer"
        elif needs_approval:
            waiting_source = "approval"
        elif check_status == "pending":
            waiting_source = "checking"
        elif pr.get("draft"):
            waiting_source = "draft"
        else:
            waiting_source = "ci"
        building_id = self._pr_building_id(rec)
        return {
            "number": number,
            "buildingId": building_id,
            "title": human_proposal_title(pr.get("title") or "", self._attribute_labels),
            "technicalTitle": pr.get("title") or "",
            "kind": kind,
            "author": ((pr.get("user") or {}).get("login") or ""),
            "draft": bool(pr.get("draft")),
            "state": pr.get("state") or "open",
            "updated": pr.get("updated_at") or "",
            "url": pr.get("html_url") or "",
            "queueStatus": queue_status,
            "waitingSource": waiting_source,
            "adjustmentReasons": adjustment_reasons,
            "autoResubmit": auto_resubmit,
            "reviewerFeedback": reviewer_feedback,
            "behind": behind,   # a note (GitHub's "Update branch"), never a hold
        }

    def review_queue(self) -> dict:
        token, login, nwo = self._review_identity()
        # All open PRs; a page GitHub refuses is an error the screen shows.
        pulls = github_list(token, f"/repos/{nwo}/pulls?state=open&sort=updated&direction=desc",
                            strict=True)

        candidates = [
            pr for pr in pulls
            if str((pr.get("base") or {}).get("ref") or "") == "main"
        ]
        awaiting = self._awaiting_approval(token, nwo) if candidates else frozenset()
        workers = min(8, max(1, len(candidates)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            items = [
                item for item in pool.map(
                    lambda pr: self._review_queue_item(token, nwo, pr, awaiting), candidates
                ) if item is not None
            ]
        perm = self.reviewer_permission()
        return {"ok": True, "nwo": nwo, "login": login, **perm, "items": items}

    def _change_rows(self, body: str, comments: list, kind: str) -> list[dict]:
        sources = [body]
        sources += [
            str(c.get("body") or "") for c in comments
            if pr_markers.CHANGE_SUMMARY in str(c.get("body") or "")
        ]
        words = change_table_words()
        for source in sources:
            for table in markdown_tables(source):
                heads = table["headers"]
                before_key = next((h for h in heads if h in words["before"]), None)
                after_key = next((h for h in heads if h in words["after"]), None)
                if not before_key or not after_key:
                    continue
                label_key = next((h for h in heads if h in words["label"]), None)
                rows = []
                for i, row in enumerate(table["rows"], 1):
                    raw_label = row.get(label_key, "") if label_key else ""
                    if kind == "texture":
                        label = tr("hub.change_texture_row", "Facade image {n}", n=i)
                    else:
                        label = (
                            attribute_label(raw_label, self._attribute_labels)
                            if raw_label
                            else tr("hub.change_row", "Changed item {n}", n=i)
                        )
                    rows.append({
                        "label": label,
                        "before": row.get(before_key, ""),
                        "after": row.get(after_key, ""),
                    })
                if rows:
                    return rows
        return []

    def building_history(self, building_id: str, current_number: int, current_pr: dict) -> list[dict]:
        """Return past and current change proposals containing the same building ID, newest first."""
        token, _login, nwo = self._review_identity()
        found: list[dict] = []
        if building_id:
            query = urlencode({
                "q": f'repo:{nwo} is:pr "{building_id}"',
                "sort": "updated", "order": "desc", "per_page": "20",
            })
            code, result = runtime.github_api(f"/search/issues?{query}", token)
            if code == 200 and isinstance(result, dict):
                found = [x for x in (result.get("items") or []) if isinstance(x, dict)]
        if not any(int(x.get("number") or 0) == current_number for x in found):
            found.insert(0, current_pr)

        history: list[dict] = []
        for item in found:
            number = int(item.get("number") or 0)
            kind = review_kind(item)
            if number != current_number and kind == "other":
                continue
            pull_meta = item.get("pull_request") or {}
            merged = bool(item.get("merged_at") or pull_meta.get("merged_at"))
            state = str(item.get("state") or "").lower()
            if number == current_number and state == "open":
                status = tr("hub.history_current_open", "This proposal — awaiting review")
            elif number == current_number:
                status = tr("hub.history_current", "The proposal shown now")
            elif merged:
                status = tr("hub.history_merged", "Approved and applied")
            elif state == "open":
                status = tr("hub.history_open", "Awaiting review")
            else:
                status = tr("hub.history_closed", "Closed")
            history.append({
                "number": number,
                "title": human_proposal_title(
                    item.get("title") or tr("hub.proposal_title_fallback", "Change record"),
                    self._attribute_labels
                ),
                "kind": kind,
                "status": status,
                "current": number == current_number,
                "updated": item.get("updated_at") or item.get("created_at") or "",
                "url": item.get("html_url") or "",
            })
        history.sort(key=lambda x: (not x["current"], str(x["updated"])), reverse=False)
        if history:
            current = [x for x in history if x["current"]]
            older = sorted(
                [x for x in history if not x["current"]],
                key=lambda x: str(x["updated"]), reverse=True,
            )
            history = current + older
        return history

    def review_detail(self, number: int) -> dict:
        token, login, nwo = self._review_identity()
        if number < 1:
            raise ValueError(tr("hub.err_bad_pr_number", "Invalid PR number"))

        code, pr = runtime.github_api(f"/repos/{nwo}/pulls/{number}", token)
        if code != 200 or not isinstance(pr, dict):
            raise RuntimeError(runtime.api_error(code, pr))
        rec = PrRecords(token, nwo, pr)
        files, comments, reviews, check_runs = rec.files, rec.comments, rec.reviews, rec.check_runs
        kind = review_kind(pr)
        body = str(pr.get("body") or "")
        state = self._pr_state(rec)
        verdict, reason_ok, manual_changes_requested = state["verdict"], state["reasonOk"], state["reviewerFeedback"]
        reason = human_reason(body, kind, self._attribute_labels)
        building_id = self._pr_building_id(rec)
        model_ref = self._building_ref_from_local_files(files, comments, building_id)

        unsafe_files = [f.get("filename") or "" for f in files if not self._is_data_file(str(f.get("filename") or ""))]
        checks = [{
            "name": check_display_name(c.get("name") or ""),
            "technicalName": c.get("name") or "",
            "status": c.get("status") or "",
            "conclusion": c.get("conclusion") or "",
            "url": c.get("details_url") or "",
        } for c in check_runs]
        checks_ok = verdict["status"] == "pass"   # the list above is for people; the verdict is ci-report's
        behind = pr_behind(token, nwo, pr)
        retry = ci_retry_info(verdict)
        preview_url = ""
        for comment in comments:
            cbody = str(comment.get("body") or "")
            if not preview_url and cbody.startswith(pr_markers.PREVIEW):   # the preview comment itself
                hit = re.search(r"\]\((https://[^)\s]+citygml-viewer[^)\s]*)\)", cbody)
                if hit:
                    preview_url = hit.group(1)

        local_model_url = ""
        center = model_ref.get("center")
        if model_ref.get("tile") and model_ref.get("gid"):
            local_model_url = (
                "/review-viewer.html?" + urlencode({
                    "tile": str(model_ref["tile"]), "bid": str(model_ref["gid"]),
                })
            )
        model_url = preview_url or local_model_url
        google_maps_url = ""
        google_maps_embed_url = ""
        if isinstance(center, list) and len(center) == 2:
            coordinates = f"{center[0]},{center[1]}"
            google_maps_url = "https://www.google.com/maps/@?" + urlencode({
                "api": "1", "map_action": "map", "center": coordinates,
                "zoom": "20", "basemap": "satellite",
            })
            # For location checks inside the walkthrough screen. Environments that cannot open it can use the official Maps URL above.
            google_maps_embed_url = "https://maps.google.com/maps?" + urlencode({
                "q": coordinates, "z": "20", "t": "k", "output": "embed",
            })

        change_rows = self._change_rows(body, comments, kind)
        if kind == "texture":
            gml_file = next(
                (str(f.get("filename") or "") for f in files
                 if str(f.get("filename") or "").lower().endswith(".gml")),
                "",
            )
            bldg_dir = gml_file.rsplit("/", 1)[0] if "/" in gml_file else ""
            for row in change_rows:
                for key, side in (("before", "base"), ("after", "head")):
                    rel = str(row.get(key) or "")
                    if bldg_dir and rel and rel not in no_texture_placeholders():
                        full_path = f"{bldg_dir}/{rel}"
                        row[key + "Asset"] = (
                            f"/api/reviews/{number}/asset?"
                            + urlencode({"side": side, "path": full_path})
                        )

        permission = self.reviewer_permission()
        author = ((pr.get("user") or {}).get("login") or "")
        self_authored = author == login
        auto_resubmit = verdict["state"] == "fix"
        changes_requested = (
            manual_changes_requested or auto_resubmit
        )
        checkpoints = review_checkpoints(
            reason_ok=reason_ok,
            unsafe_files=unsafe_files,
            verdict=verdict,
            model_available=bool(model_url),
            kind=kind,
            changes_requested=changes_requested,
            adjustment_owner="reviewer" if manual_changes_requested else "ci",
            behind=behind,
        )
        # warn rows are advisory and never block (A6); the verdict is CI's, row by row
        row = {c["key"]: c["status"] for c in checkpoints}
        inspection_ready = all(c["status"] in ("pass", "na", "warn") for c in checkpoints)
        failed_inspections = [c["label"] for c in checkpoints
                              if c["status"] in ("fail", "error") and c["key"] not in ("reason", "file-scope")]
        blockers = []
        if str(pr.get("state") or "").lower() != "open":
            blockers.append(tr("hub.blocker_not_open", "This proposal is not open"))
        if pr.get("draft"):
            blockers.append(tr("hub.blocker_draft", "This proposal is a draft"))
        if not checks_ok:
            blockers.append(tr(
                "hub.blocker_checks",
                "Approval is not possible until the automated checks complete and all succeed"))
        if row.get("file-scope") in ("fail", "error"):
            blockers.append(tr(
                "hub.blocker_unsafe_files",
                "Changes to files other than building data are included"))
        if row.get("reason") in ("fail", "error"):
            blockers.append(tr(
                "hub.blocker_no_reason",
                "The reason for the change and supporting documents are not filled in"))
        if manual_changes_requested:
            blockers.append(tr(
                "hub.blocker_reviewer_wait",
                "Waiting for a response to the reviewer's comment"))
        # Do not repeat this when the check-run blocker is already shown
        if failed_inspections and checks_ok:
            blockers.append(tr(
                "hub.blocker_failed_inspections",
                "There are inspection items that have not passed ({items})",
                items=tr("hub.list_separator", ", ").join(failed_inspections)))
        if self_authored:
            blockers.append(tr(
                "hub.blocker_self", "You cannot approve a proposal you submitted yourself"))
        if not permission["canReview"]:
            blockers.append(tr(
                "hub.blocker_permission",
                "Sign in with an account that has approval permission"))

        return {
            "ok": True,
            "number": number,
            "buildingId": building_id,
            "title": human_proposal_title(pr.get("title") or "", self._attribute_labels),
            "technicalTitle": pr.get("title") or "",
            "body": body,
            "kind": kind,
            "author": author,
            "state": pr.get("state") or "",
            "draft": bool(pr.get("draft")),
            "url": pr.get("html_url") or "",
            "headSha": (pr.get("head") or {}).get("sha") or "",
            "updated": pr.get("updated_at") or "",
            "reason": reason,
            "reasonOk": reason_ok,
            "evidenceLinks": evidence_links(pr_reason.section(body) or body),
            "history": self.building_history(building_id, number, pr),
            "changes": change_rows,
            "files": [{
                "path": f.get("filename") or "",
                "status": f.get("status") or "",
                "additions": f.get("additions") or 0,
                "deletions": f.get("deletions") or 0,
                "patch": f.get("patch") or "",
            } for f in files],
            "unsafeFiles": unsafe_files,
            "checks": checks,
            "checksOk": checks_ok,
            "retry": retry,
            "checkpoints": checkpoints,
            "allGreen": inspection_ready,
            "inspectionReady": inspection_ready,
            "autoResubmit": auto_resubmit,
            "behind": behind,   # a note (GitHub's "Update branch"), never a hold
            "reviewerFeedback": manual_changes_requested,
            "changesRequested": changes_requested,
            "previewUrl": preview_url,
            "modelUrl": model_url,
            "modelIsComparison": bool(preview_url),
            "center": center,
            "googleMapsUrl": google_maps_url,
            "googleMapsEmbedUrl": google_maps_embed_url,
            "alreadyApproved": any(r.get("state") == "APPROVED" for r in reviews),
            "permission": permission["permission"],
            "selfAuthored": self_authored,
            "blockers": blockers,
            "canApprove": not blockers,
            # The walkthrough does not modify the real PR; the operation is experienced in the same screen.
            "canDemoApprove": inspection_ready,
        }

    def review_asset(self, number: int, side: str, path: str) -> tuple[bytes, str]:
        """Return the before/after images of a texture PR via the authenticated API."""
        if side not in ("base", "head"):
            raise ValueError(tr("hub.err_bad_side", "Invalid image comparison side"))
        if not self._is_data_file(path, gml=False):
            raise ValueError(tr("hub.err_not_texture", "Only texture images can be fetched"))
        token, _login, nwo = self._review_identity()
        code, pr = runtime.github_api(f"/repos/{nwo}/pulls/{number}", token)
        if code != 200 or not isinstance(pr, dict):
            raise RuntimeError(runtime.api_error(code, pr))
        ref = str((pr.get(side) or {}).get("sha") or (pr.get(side) or {}).get("ref") or "")
        if not ref:
            raise RuntimeError(tr("hub.err_no_image_ref", "Could not resolve the image reference"))
        endpoint = f"/repos/{nwo}/contents/{quote(path, safe='/')}?ref={quote(ref, safe='')}"
        status, data, mime = runtime.github_raw(endpoint, token)
        if status != 200:
            raise FileNotFoundError(tr(
                "hub.err_image_fetch", "Could not fetch the image (HTTP {status})",
                status=status))
        if not mime.startswith("image/"):
            mime = runtime.media_type(path)
        return data, mime

    def submit_review(self, number: int, *, demo: bool = False) -> dict:
        detail = self.review_detail(number)
        if demo:
            if not detail["canDemoApprove"]:
                raise RuntimeError(tr(
                    "hub.err_demo_not_ready",
                    "This proposal is not in a state that can be approved, "
                    "even in the walkthrough"))
            return {
                "ok": True,
                "demo": True,
                "number": number,
                "reviewer": self._review_identity()[1],
            }
        if not detail["canApprove"]:
            raise RuntimeError(tr(
                "hub.err_cannot_approve", "Cannot approve: {blockers}",
                blockers=tr("hub.blocker_separator", "; ").join(detail["blockers"])))
        token, login, nwo = self._review_identity()
        code, data = runtime.github_api(
            f"/repos/{nwo}/pulls/{number}/reviews",
            token,
            method="POST",
            payload={
                "commit_id": detail["headSha"],
                "body": "Confirmed the changes, supporting evidence, and "
                        "automated inspection results in the review screen.",
                "event": "APPROVE",
            },
        )
        if code != 200:
            raise RuntimeError(runtime.api_error(code, data))
        return {"ok": True, "demo": False, "number": number, "reviewer": login}

    def submit_review_feedback(
        self, number: int, message: str, *, demo: bool = False
    ) -> dict:
        """Record the approver's confirmation comment as a change-request review on the current version."""
        message = str(message or "").strip()
        if len(message) < 5:
            raise ValueError(tr(
                "hub.err_feedback_short",
                "Enter what you want confirmed in at least 5 characters"))
        if len(message) > 1000:
            raise ValueError(tr(
                "hub.err_feedback_long", "Enter the comment in at most 1000 characters"))
        detail = self.review_detail(number)
        token, login, nwo = self._review_identity()
        if demo:
            return {
                "ok": True, "demo": True, "number": number,
                "reviewer": login, "message": message,
            }
        if str(detail.get("state") or "").lower() != "open":
            raise RuntimeError(tr(
                "hub.err_closed_comment", "You cannot comment on a closed proposal"))
        if detail.get("selfAuthored"):
            raise RuntimeError(tr(
                "hub.err_self_request",
                "You cannot request changes on a proposal you submitted yourself"))
        if detail.get("permission") not in ("admin", "maintain", "push", "write"):
            raise RuntimeError(tr(
                "hub.blocker_permission",
                "Sign in with an account that has approval permission"))
        code, data = runtime.github_api(
            f"/repos/{nwo}/pulls/{number}/reviews",
            token,
            method="POST",
            payload={
                "commit_id": detail["headSha"],
                "body": "## 💬 Confirmation from the reviewer\n\n" + message,
                "event": "REQUEST_CHANGES",
            },
        )
        if code != 200:
            raise RuntimeError(runtime.api_error(code, data))
        return {
            "ok": True, "demo": False, "number": number,
            "reviewer": login, "message": message,
        }

    def request_ci_retry(self, number: int, *, demo: bool = False) -> dict:
        """Request a re-run, via a PR comment, for temporary inspection failures only. Who may
        ask is CI's rule alone (A8): the hub posts the comment and CI replies on the PR."""
        if not demo:
            runtime.require_min_hub(self.root)
        token, login, nwo = self._review_identity()
        code, pr = runtime.github_api(f"/repos/{nwo}/pulls/{number}", token)
        if code != 200 or not isinstance(pr, dict):
            raise RuntimeError(runtime.api_error(code, pr))
        if str(pr.get("state") or "").lower() != "open":
            raise RuntimeError(tr(
                "hub.err_closed_retry", "A closed proposal cannot be re-checked"))

        head_sha = str((pr.get("head") or {}).get("sha") or "")
        check_runs = github_list(token, f"/repos/{nwo}/commits/{head_sha}/check-runs", "check_runs")
        retry = ci_retry_info(ci_verdict(check_runs, pr))
        if not retry["available"]:
            raise RuntimeError(retry["reason"])
        if demo:
            return {"ok": True, "demo": True, "number": number, "retry": retry}

        marker = pr_markers.RETRY_REQUEST
        comment = (
            f"{marker}\n"
            "## 🔄 Re-run automated inspection\n\n"
            f"Received a re-inspection request from @{login} for the current"
            f" version (`{head_sha[:12]}`).\n"
            "The data is unchanged; only the inspection runs again."
        )
        post_code, data = runtime.github_api(
            f"/repos/{nwo}/issues/{number}/comments",
            token,
            method="POST",
            payload={"body": comment},
        )
        if post_code != 201:
            raise RuntimeError(str(data.get("message") or f"HTTP {post_code}"))
        self._contributions.clear()
        return {
            "ok": True, "demo": False, "number": number,
            "message": tr("hub.retry_accepted_msg",
                          "The request was posted. CI answers on the proposal: it starts "
                          "the checks again within a few minutes, or says why it cannot"),
        }
