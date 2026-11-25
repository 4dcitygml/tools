# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""The editor's send path with and without an account (hub-v1.2.1), on real git repos.

Reuses the upstream/clone fixture of test_upstream_sync: a local upstream, a clone,
and a proposal payload. GitHub's REST API is stubbed; git runs for real.
"""
from __future__ import annotations

import os
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_upstream_sync as fx
from tests.support import REPO_ROOT, TOKYO, accounts, ready_to_send, runtime

attr, git = fx.attr, fx.git


CODELIST_411 = ('<gml:Dictionary xmlns:gml="http://www.opengis.net/gml"><gml:dictionaryEntry><gml:Definition>'
                '<gml:name>411</gml:name><gml:description>residential</gml:description>'
                '</gml:Definition></gml:dictionaryEntry></gml:Dictionary>')

PAYLOAD = {
    "tile": "53394611", "gid": "gml-bldg-1",
    "reason": "現地調査票で地上階数を確認しました。",
    "changes": [{"key": "storeysAboveGround#0", "tag": "storeysAboveGround", "index": 0,
                 "old": "2", "new": "3", "label": "地上階数"}],
    "sourceSelections": [{"key": "storeysAboveGround#0", "code": "801"}],
}


class _PushFixture(fx._SyncFixture):
    """The clone under a temporary HOME (account store, settings; English UI); GitHub REST stubbed."""

    def setUp(self):
        super().setUp()
        self.acc = accounts
        self.api_calls = []
        self.api_response = (201, {"html_url": "https://github.com/4dcitygml/sample-tokyo-station/pull/7"})

        def api(path, token, method="GET", payload=None, timeout=30):
            self.api_calls.append((method, path, token))
            return self.api_response
        self._api = patch.object(runtime, "github_api", api)
        self._api.start()
        self.repo = attr.Repo(self.clone)
        self.payload = dict(PAYLOAD)

    def tearDown(self):
        self._api.stop()
        super().tearDown()


class TestPushWithoutAccount(_PushFixture):
    """No account: never the computer's credentials; a failed push explains the way out."""

    def test_a_send_without_an_account_stops_before_anything_is_written(self):
        # P1: the send checks the confirmation's rule itself; it used to push and fail at the push
        git(self.clone, "remote", "set-url", "origin", str(Path(self.temp.name) / "nowhere.git"))
        head = git(self.clone, "rev-parse", "HEAD")
        gml = self.clone / "city/udx/bldg/53394611_bldg_6697_op.gml"
        before = gml.read_text(encoding="utf-8")
        self.assertIsNone(accounts.login_for_clone(self.clone))
        with self.assertRaises(RuntimeError) as ctx:
            self.repo.create_pr(self.payload)
        self.assertIn("choose the GitHub account for this city", str(ctx.exception))
        self.assertIn("Settings", str(ctx.exception))
        # nothing happened: on main, no edit branch, file untouched
        self.assertEqual(git(self.clone, "rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertEqual(git(self.clone, "rev-parse", "HEAD"), head)
        self.assertEqual(gml.read_text(encoding="utf-8"), before)
        self.assertNotIn("edit/", git(self.clone, "branch", "--list"))

    def test_a_detached_clone_comes_back_where_it_was(self):
        # D-3b5: the way back was `rev-parse --abbrev-ref HEAD`, which says "HEAD" when detached;
        # checking that out again left the clone on the edit branch
        git(self.clone, "remote", "set-url", "origin", str(Path(self.temp.name) / "nowhere.git"))
        head = git(self.clone, "rev-parse", "HEAD")
        git(self.clone, "checkout", "-q", "--detach")
        with patch.object(attr.Repo, "_fresh_pr_base", lambda self, rel: head), self.assertRaises(RuntimeError):
            self.repo.create_pr(self.payload)
        self.assertEqual(git(self.clone, "rev-parse", "--abbrev-ref", "HEAD"), "HEAD")
        self.assertEqual(git(self.clone, "rev-parse", "HEAD"), head)

    def test_texture_editor_without_an_account_explains_the_way_out(self):
        # hub-v1.4.0 D2: the texture editor asked attr.current_login(), which does not exist, so a
        # send without an account raised AttributeError (a 500) instead of this message
        from tests.support import load_app
        tex = load_app("tex_push_paths", "tools/tex_editor/app.py")
        git(self.clone, "remote", "set-url", "origin", str(Path(self.temp.name) / "nowhere.git"))
        head = git(self.clone, "rev-parse", "HEAD")
        repo = tex.TexRepo(self.clone)
        gml = self.clone / "city/udx/bldg/53394611_bldg_6697_op.gml"

        def apply_textures(self, code, gid, images):   # the texturing itself is not under test
            gml.write_text(gml.read_text(encoding="utf-8") + "<!-- textured -->", encoding="utf-8")
            return {"added": [], "replaced": [], "new": True}
        with patch.object(tex.TexRepo, "_fresh_pr_base", lambda self, rel: head), \
                patch.object(tex.TexRepo, "apply_textures", apply_textures):
            with self.assertRaises(RuntimeError) as ctx:
                repo.create_pr({"tile": "53394611", "gid": "gml-bldg-1", "images": [], "faceCount": 1,
                                    "consentCC0": True, "reason": "Facade photo taken on site"})
        self.assertIn("choose the GitHub account for this city", str(ctx.exception))
        self.assertEqual(git(self.clone, "rev-parse", "--abbrev-ref", "HEAD"), "main")

    def test_a_texture_proposal_needs_its_reason(self):
        # D-3b8: the texture send had no reason check; an empty reason went out and failed in CI
        from tests.support import load_app
        tex = load_app("tex_reason", "tools/tex_editor/app.py")
        repo, applied = tex.TexRepo(self.clone), []
        with patch.object(tex.TexRepo, "apply_textures", lambda self, *a: applied.append(a)):
            for reason in ("", "   ", "ok"):
                with self.assertRaises(ValueError) as ctx:
                    repo.create_pr({"tile": "53394611", "gid": "gml-bldg-1", "images": [{}], "faceCount": 1,
                                    "consentCC0": True, "reason": reason})
                self.assertIn("at least 5 characters", str(ctx.exception))
        self.assertEqual(applied, [])

    def test_network_git_never_consults_the_computers_helpers(self):
        args = runtime.git_args(net=True, store=accounts.store_for(accounts.login_for_clone(self.clone)))
        self.assertIn("credential.helper=", args)
        self.assertNotIn("manager", " ".join(args))

    def test_confirmation_blocks_the_send_before_anything_happens(self):
        info = self.repo.submission_info()
        self.assertIsNone(info["account"])
        self.assertFalse(info["autoPr"])
        self.assertIn("choose the GitHub account", info["blocked"])   # V6: the server says why, for both editors



class TestOtherChanges(_PushFixture):
    """D-3b2: the check for other changed data files before a send. It looked at one folder
    (the package that holds the buildings), missed git's quoted names (non-ASCII ones), and on
    Windows counted the target itself, whose path came with backslashes."""

    TARGET = "city/udx/bldg/53394611_bldg_6697_op.gml"

    def commit(self, rel: str, text: str = "x") -> None:
        (self.clone / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.clone / rel).write_text(text, encoding="utf-8")
        git(self.clone, "add", rel)
        git(self.clone, "commit", "-qm", rel)

    def test_the_target_is_never_another_change(self):
        (self.clone / self.TARGET).write_text("changed", encoding="utf-8")
        self.assertEqual(self.repo._other_data_changes(self.TARGET), [])
        self.assertEqual(self.repo._other_data_changes(self.TARGET.replace("/", "\\")), [])   # Windows

    def test_every_declared_folder_and_every_name_counts(self):
        self.commit("4dcitygml.json", (self.clone / "4dcitygml.json").read_text(encoding="utf-8")
                    .replace('"data_dirs": ["city"]', '"data_dirs": ["city", "more"]'))
        self.commit("more/b.gml")
        self.commit("city/udx/bldg/建物.gml")
        self.commit("docs/note.txt")
        for rel in ("more/b.gml", "city/udx/bldg/建物.gml", "docs/note.txt"):
            (self.clone / rel).write_text("changed", encoding="utf-8")
        (self.clone / "more/new.gml").write_text("untracked", encoding="utf-8")
        self.assertEqual(sorted(attr.Repo(self.clone)._other_data_changes(self.TARGET)),
                         ["city/udx/bldg/建物.gml", "more/b.gml"])


class TestTheShownId(_PushFixture):
    """D-3b3: the card, the tooltip and the texture editor showed the uro:buildingID leaf, while
    the branch, the trailer and the pretest used the city's rule. One rule for all of them."""

    def shown(self, rule: str) -> str:
        gml = self.clone / "city/udx/bldg/53394611_bldg_6697_op.gml"   # the ID as PLATEAU nests it
        gml.write_text(gml.read_text(encoding="utf-8").replace(
            "<uro:buildingID>bldg-1</uro:buildingID>",
            "<uro:buildingIDAttribute><uro:BuildingIDAttribute><uro:buildingID>bldg-1</uro:buildingID>"
            "</uro:BuildingIDAttribute></uro:buildingIDAttribute>"), encoding="utf-8")
        cfg = self.clone / "4dcitygml.json"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace('{"type": "uro:buildingID"}', rule),
                       encoding="utf-8")
        return attr.Repo(self.clone).building_json("53394611", "gml-bldg-1")["buildingID"]

    def test_the_card_shows_the_citys_id(self):
        self.assertEqual(self.shown('{"type": "uro:buildingID"}'), "bldg-1")
        self.assertEqual(self.shown('{"type": "gml:id"}'), "gml-bldg-1")
        self.assertEqual(self.shown('{"type": "uro:buildingID", "invalid_values": ["bldg-1"]}'), "gml-bldg-1")



class TestSourcesAreTheDialects(_PushFixture):
    def test_a_plain_city_records_no_sources(self):
        # the source-note convention (the set, its code list) is the PLATEAU pack's (Pack.sources)
        tile = "53394611"
        self.assertIsNotNone(self.repo.tile(tile)["buildings"]["gml-bldg-1"]["src"])
        cfg = self.clone / "4dcitygml.json"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace('"dialect": "plateau", "data_dirs": ["city"]',
                                                               '"data_dirs": ["city/udx/bldg"]'), encoding="utf-8")
        import shutil
        shutil.copytree(self.clone / "city/codelists", self.clone / "city/udx/bldg/codelists")   # the code list is there
        plain = attr.Repo(self.clone)
        self.assertIn("DataQualityAttribute_thematicSrcDesc.xml", plain.codelists())
        self.assertIsNone(plain.tile("53394611_bldg_6697_op")["buildings"]["gml-bldg-1"]["src"])

class TestTheCodeListOfAValue(_PushFixture):
    """The editor finds a value's list as CI does (scripts/codelists.resolve): the relative codeSpace against
    the .gml file's folder, wherever the file is; no list (external, missing, broken) is free input."""

    def leaves(self, usage_spaces):
        gml = self.clone / "city/udx/bldg/53394611_bldg_6697_op.gml"
        coded = "".join(f'<bldg:usage codeSpace="{cs}">411</bldg:usage>' for cs in usage_spaces)
        gml.write_text(gml.read_text(encoding="utf-8").replace("</bldg:Building>", coded + "</bldg:Building>"),
                       encoding="utf-8")
        repo = attr.Repo(self.clone)
        building = repo.tile("53394611")["buildings"]["gml-bldg-1"]
        return repo, [lf for g in building["items"] for lf in g["leaves"] if lf["tag"] == "usage"]

    def write_list(self, rel, text=CODELIST_411):
        path = self.clone / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_the_standard_layout_keeps_its_key(self):
        self.write_list("city/codelists/Building_usage.xml")
        repo, (leaf,) = self.leaves(["../../codelists/Building_usage.xml"])
        self.assertEqual(leaf["codelist"], "Building_usage.xml")
        self.assertEqual(repo.codelists()["Building_usage.xml"], {"411": "residential"})

    def test_a_list_outside_the_codelists_folder_is_reached_by_the_relative_codespace(self):
        self.write_list("shared/Other.xml")
        repo, (leaf,) = self.leaves(["../../../shared/Other.xml"])
        self.assertIsNotNone(leaf["codelist"])
        self.assertNotIn(leaf["codelist"], repo.codelists())          # not under <data_root>/codelists
        tile = repo.tile_json("53394611")
        self.assertEqual(tile["codelists"], {leaf["codelist"]: {"411": "residential"}})   # the page gets its table

    def test_no_list_is_free_input_exactly_where_ci_checks_nothing(self):
        self.write_list("city/codelists/Building_usage.xml")
        self.write_list("city/codelists/Broken.xml", "<gml:Dictionary")
        self.write_list("city/codelists/WithDoctype.xml", '<!DOCTYPE x [<!ENTITY a "b">]>' + CODELIST_411)
        _, leaves = self.leaves(["../../codelists/Missing.xml", "http://example.com/Building_usage.xml",
                                 "../../codelists/Broken.xml", "../../codelists/WithDoctype.xml",
                                 "Building_usage.xml"])    # the file name alone is not a path to the list
        self.assertEqual([lf["codelist"] for lf in leaves], [None] * 5)


class TestTheTileCache(_PushFixture):
    def test_a_file_changed_by_another_process_is_read_again(self):
        # D-3c9: a tile parsed once stayed cached after the hub's sync (another process) changed it
        gml = self.clone / "city/udx/bldg/53394611_bldg_6697_op.gml"
        self.assertEqual(self.repo.building_json("53394611", "gml-bldg-1")["buildingID"], "bldg-1")
        gml.write_text(gml.read_text(encoding="utf-8").replace(">bldg-1<", ">bldg-22<"), encoding="utf-8")
        self.assertEqual(self.repo.building_json("53394611", "gml-bldg-1")["buildingID"], "bldg-22")

class TestPushWithAccount(_PushFixture):
    """An account bound to the clone's city: identity in the clone, push with the account's store."""

    def setUp(self):
        super().setUp()
        ready_to_send(self.repo)   # origin stays the local upstream fixture for the push

    def test_send_uses_the_account_end_to_end(self):
        info = self.repo.submission_info()
        self.assertEqual((info["account"], info["autoPr"], info["origin"]), ("alice", True, "alice/sample-tokyo-station"))
        self.assertEqual(info["identity"], {"name": "alice", "email": "1+alice@users.noreply.github.com"})
        result = self.repo.create_pr(self.payload)
        self.assertTrue(result["ok"] and result["pushed"], result)
        self.assertEqual(result.get("prUrl"), "https://github.com/4dcitygml/sample-tokyo-station/pull/7")
        # the commit carries the account's identity, not the computer's
        author = git(self.clone, "log", "-1", "--format=%an <%ae>", result["branch"])
        self.assertEqual(author, "alice <1+alice@users.noreply.github.com>")
        # the proposal was opened with the account's token, from the fork's owner
        method, path, token = self.api_calls[-1]
        self.assertEqual((method, token), ("POST", "tok-a"))
        self.assertTrue(path.endswith("/pulls"))

    def test_a_rebind_mid_send_does_not_split_push_and_pr(self):
        # D-3c6: the editor read the login three times a send; a rebind by the hub in between
        # pushed as one account and opened the PR as another. One read per send now.
        self.acc.save_account("bob", "tok-b", 2)
        real = self.repo._git

        def git_then_rebind(*args, **kw):
            if args[:1] == ("push",):
                self.acc.bind_city_login(TOKYO, "bob")   # the hub rebinds the city meanwhile
            return real(*args, **kw)
        with patch.object(self.repo, "_git", git_then_rebind):
            result = self.repo.create_pr(self.payload)
        self.assertTrue(result["pushed"])
        self.assertEqual(self.api_calls[-1][2], "tok-a")              # the PR as the account that pushed
        self.assertEqual(self.repo.login, "bob")                      # after the send: the new binding

    def test_overlapping_sends_keep_their_own_account(self):
        # B04: the account was pinned on the shared Repo before the send lock was taken; a second
        # request (each runs in its own server thread) overwrote the first send's account, and the
        # first send's end cleared the second's. Now the lock comes first and the pin is the thread's.
        self.acc.save_account("bob", "tok-b", 2)
        inside, release = threading.Event(), threading.Event()
        real = self.repo._other_data_changes
        results = {}

        def held_in_first(rel):
            if threading.current_thread().name == "first":
                inside.set()
                release.wait(10)
            return real(rel)

        def send(name):
            try:
                results[name] = self.repo.create_pr(dict(self.payload))
            except Exception as exc:   # reported by the assertions below
                results[name] = exc
        ticks = iter(range(10))

        class Clock(attr.datetime):   # two sends within one second would name the same branch
            @classmethod
            def now(cls, tz=None):
                return attr.datetime(2026, 10, 6, 12, 0, next(ticks), tzinfo=tz)
        with patch.object(self.repo, "_other_data_changes", held_in_first), patch.object(attr, "datetime", Clock):
            first = threading.Thread(target=send, args=("first",), name="first")
            first.start()
            self.assertTrue(inside.wait(10))
            self.acc.bind_city_login(TOKYO, "bob")                  # the hub rebinds the city meanwhile
            second = threading.Thread(target=send, args=("second",), name="second")
            second.start()
            self.assertEqual(self.repo.login, "bob")                # another request reads the binding
            release.set()
            first.join(30)
            second.join(30)
        self.assertTrue(results["first"]["pushed"], results["first"])
        prs = [token for method, path, token in self.api_calls if path.endswith("/pulls")]
        self.assertEqual(prs, ["tok-a"])                          # the first entirely as alice
        # the second reads bob under the lock; origin is still alice's copy (the hub repoints it once
        # the lock is free), so the send is refused instead of pushing as bob to alice's copy (P1)
        self.assertIsInstance(results["second"], RuntimeError)
        self.assertIn("upload target", str(results["second"]))
        self.assertEqual(self.repo.login, "bob")

    def test_a_failed_send_leaves_no_account_pinned(self):
        with self.assertRaises(RuntimeError), patch.object(self.repo, "_other_data_changes", lambda rel: ["x.gml"]):
            self.repo.create_pr(self.payload)
        self.acc.save_account("bob", "tok-b", 2)
        self.acc.bind_city_login(TOKYO, "bob")
        self.assertEqual(self.repo.login, "bob")

    def test_the_confirmation_is_ready_only_when_the_send_is(self):
        # V6: the screens judged "ready" themselves, from a name or an e-mail, while the send
        # refuses a clone without both; the server decides now, with the send's own rule
        git(self.clone, "remote", "set-url", "origin", "https://github.com/alice/sample-tokyo-station.git")
        self.assertEqual(self.repo.submission_info()["blocked"], "")
        git(self.clone, "config", "--unset", "user.email")
        self.assertIn("choose the GitHub account", self.repo.submission_info()["blocked"])
        self.acc.apply_clone_identity(self.clone, "alice", 1)
        self.repo._origin_nwo = lambda: "someone/sample-tokyo-station"
        self.assertIn("upload target", self.repo.submission_info()["blocked"])

    def test_a_missing_identity_stops_the_send_before_the_edit_is_written(self):
        # D-3b1: the identity was checked after apply(), whose added files (new texture images)
        # then stayed in the tree; now nothing is written
        self.acc.clear_clone_identity(self.clone)
        written = []
        with self.assertRaises(RuntimeError) as ctx:
            self.repo.send_proposal("attribute", "53394611", "gml-bldg-1",
                                    "city/udx/bldg/53394611_bldg_6697_op.gml", lambda: written.append(1))
        self.assertIn("choose the GitHub account", str(ctx.exception))
        self.assertEqual(written, [])

    def test_a_failed_send_keeps_a_tracked_image_it_reused(self):
        # D-3b1: images are named by their content, so an edit can name an image main already
        # has; the rollback deleted it with the new ones
        tracked = "city/udx/bldg/53394611_bldg_6697_appearance/tex_same.jpg"
        (self.clone / tracked).parent.mkdir(parents=True, exist_ok=True)
        (self.clone / tracked).write_bytes(b"\xff\xd8\xff\xd9")
        git(self.clone, "add", tracked)
        git(self.clone, "commit", "-qm", "an image")
        fresh = "city/udx/bldg/53394611_bldg_6697_appearance/tex_new.jpg"
        rel = "city/udx/bldg/53394611_bldg_6697_op.gml"

        def apply():
            (self.clone / fresh).write_bytes(b"\xff\xd8\xff\xd9")
            (self.clone / rel).write_text((self.clone / rel).read_text(encoding="utf-8") + "<!-- x -->",
                                          encoding="utf-8")
            return attr.Edit(lambda bid: ("m", "t", "b"), added=[tracked, fresh])
        git(self.clone, "remote", "set-url", "origin", str(Path(self.temp.name) / "nowhere.git"))
        head = git(self.clone, "rev-parse", "HEAD")
        with patch.object(attr.Repo, "_fresh_pr_base", lambda self, rel: head), self.assertRaises(RuntimeError):
            self.repo.send_proposal("attribute", "53394611", "gml-bldg-1", rel, apply)
        self.assertTrue((self.clone / tracked).is_file())
        self.assertFalse((self.clone / fresh).exists())
        self.assertEqual(git(self.clone, "status", "--porcelain"), "")

    def test_shell_identity_overrides_do_not_reach_the_commit(self):
        # A terminal that exports GIT_AUTHOR_EMAIL (or VS Code's askpass) must not steer git:
        # the editors scrub these at start; here the scrubber is applied to the test process.
        saved = {k: os.environ.get(k) for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL", "GIT_ASKPASS")}
        os.environ.update({"GIT_AUTHOR_NAME": "Other Person", "GIT_AUTHOR_EMAIL": "other@example.com",
                           "GIT_COMMITTER_EMAIL": "other@example.com", "GIT_ASKPASS": "/usr/bin/false"})
        try:
            self.acc.scrub_git_env()
            result = self.repo.create_pr(self.payload)
            author = git(self.clone, "log", "-1", "--format=%an <%ae> %cn <%ce>", result["branch"])
            self.assertEqual(author, "alice <1+alice@users.noreply.github.com> alice <1+alice@users.noreply.github.com>")
            self.assertEqual(os.environ.get("GIT_TERMINAL_PROMPT"), "0")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            os.environ.pop("GIT_TERMINAL_PROMPT", None)

    def test_no_gh_cli_fallback_ever(self):
        for path in ("tools/attr_editor/app.py", "tools/tex_editor/app.py"):
            src = (REPO_ROOT / path).read_text(encoding="utf-8")
            self.assertNotIn('"gh", "pr", "create"', src, path)
            self.assertNotIn("gh pr create", src, path)

    def test_upload_target_must_be_the_accounts_copy(self):
        # origin = the city itself (or someone else's fork) → the confirmation blocks the send
        self.repo._origin_nwo = lambda: "4dcitygml/sample-tokyo-station"
        self.assertFalse(self.repo.submission_info()["originOk"])
        self.repo._origin_nwo = lambda: "alice/sample-tokyo-station"
        self.repo._origin_url = lambda: "git@github.com:alice/sample-tokyo-station.git"
        self.assertFalse(self.repo.submission_info()["originOk"])         # SSH: the account's store cannot apply
        self.repo._origin_url = lambda: "https://github.com/Alice/sample-tokyo-station.git"
        self.assertTrue(self.repo.submission_info()["originOk"])

    def test_a_send_to_a_copy_that_is_not_the_accounts_is_refused_before_anything_is_written(self):
        # P1: the confirmation blocked this, the send did not (a direct call, or a rebind between
        # the confirmation and the click, pushed to whatever origin was)
        self.repo._origin_nwo = lambda: "someone/sample-tokyo-station"
        head = git(self.clone, "rev-parse", "HEAD")
        written = []
        with self.assertRaises(RuntimeError) as ctx:
            self.repo.send_proposal("attribute", "53394611", "gml-bldg-1",
                                    "city/udx/bldg/53394611_bldg_6697_op.gml", lambda: written.append(1))
        self.assertEqual(str(ctx.exception), self.repo.submission_info()["blocked"])   # one rule, one text
        self.assertIn("upload target", str(ctx.exception))
        self.assertEqual((written, git(self.clone, "rev-parse", "HEAD")), ([], head))
        self.assertFalse(any(path.endswith("/pulls") for _, path, _ in self.api_calls))

    def test_city_binding_beats_the_launch_environment(self):
        import json as _json
        (self.clone / "4dcitygml.json").write_text(_json.dumps({"repo": "4dcitygml/sample-tokyo-station"}))
        self.acc.save_account("bob", "tok-b", 2)
        self.acc.bind_city_login("4dcitygml/sample-tokyo-station", "bob")   # the hub rebound the city
        self.assertEqual(accounts.login_for_clone(self.clone), "bob")                        # env still says alice
        self.acc.bind_city_login("4dcitygml/sample-tokyo-station", None)    # disconnected in the hub
        self.assertIsNone(accounts.login_for_clone(self.clone))                              # the launch value does not revive it

    def test_org_restriction_is_explained_and_the_upload_is_kept(self):
        self.api_response = (403, {"message": "Although you appear to have the correct authorization credentials, "
                                              "the city organization has enabled OAuth App access restrictions"})
        result = self.repo.create_pr(self.payload)
        self.assertTrue(result["ok"] and result["pushed"], result)
        self.assertIsNone(result.get("prUrl"))
        self.assertTrue(result.get("compareUrl"))
        self.assertIn("has not approved this tool", result.get("note") or "")
        self.assertIn("Organization access", result.get("note") or "")


class TestEditorsAreStartedWithAClone(unittest.TestCase):
    """S12: the editors' own first-run setup (a copy of the hub's clone job) was never reached from
    the hub, which always passes --repo; an editor without a clone now says where to start."""

    def test_no_clone_names_the_hub(self):
        from types import SimpleNamespace
        from tests.support import load_app
        attr = load_app("attr_open_repo", "tools/attr_editor/app.py")
        args = SimpleNamespace(repo=None, data=None, textures=None)
        from tests.support import TempHome
        with TempHome(), patch.object(runtime, "detect_repo", return_value=None), \
                patch.object(runtime, "last_clone", return_value=None):   # a start migrates the settings: a temporary HOME
            with self.assertRaisesRegex(SystemExit, "Start the editors from the hub"):
                attr.open_repo(attr.Repo, args)

    def test_the_texture_editor_posts_through_the_shared_handler(self):
        src = (REPO_ROOT / "tools/tex_editor/app.py").read_text(encoding="utf-8")
        self.assertNotIn("def do_POST", src)          # its copy of the error handling is gone (C6)
        self.assertNotIn("sync_upstream_main(", src)   # no sync at start racing the hub's (S12)
        attr_src = (REPO_ROOT / "tools/attr_editor/app.py").read_text(encoding="utf-8")
        for gone in ('"/api/setup"', '"/api/edit"', '"/api/revert"', '"/codelists/"', "CloneJob("):
            self.assertNotIn(gone, attr_src)


if __name__ == "__main__":
    unittest.main()
