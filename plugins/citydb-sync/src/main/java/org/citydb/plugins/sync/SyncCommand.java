/*
 * Copyright (c) 2026 4dcitygml
 * SPDX-License-Identifier: Apache-2.0
 */
package org.citydb.plugins.sync;

import org.citydb.cli.ExecutionException;
import org.citydb.cli.extension.MainCommand;
import picocli.CommandLine;

import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

@CommandLine.Command(
        name = "sync",
        description = "Review 3DCityDB changes and propose minimal-diff GitHub pull requests.",
        mixinStandardHelpOptions = true)
public final class SyncCommand implements MainCommand {
    @CommandLine.Option(names = "--repo", paramLabel = "<dir>",
            description = "City repository (default: current directory).")
    private Path repository = Path.of("");

    @CommandLine.Option(names = "--config", paramLabel = "<file>",
            description = "Connector JSON configuration file.")
    private Path config;

    @CommandLine.Option(names = "--citygml", paramLabel = "<file>",
            description = "Reviewed CityGML file, relative to the repository.")
    private Path citygml;

    @CommandLine.Option(names = "--export-file", paramLabel = "<file>",
            description = "Use an existing 3DCityDB export (test/offline mode).")
    private Path exportFile;

    @CommandLine.Option(names = "--connector", paramLabel = "<file>",
            description = "Path to the bundled connector server.py.")
    private Path connector;

    @CommandLine.Option(names = "--python", paramLabel = "<command>",
            description = "Python command (default: PYTHON or python3).")
    private String python;

    @CommandLine.Option(names = "--citydb-command", paramLabel = "<command>",
            description = "citydb executable used for export (default: citydb).")
    private String citydbCommand = "citydb";

    @CommandLine.Option(names = "--host", paramLabel = "<host>",
            description = "Local UI bind address (default: ${DEFAULT-VALUE}).")
    private String host = "127.0.0.1";

    @CommandLine.Option(names = "--port", paramLabel = "<port>",
            description = "Local UI port (default: ${DEFAULT-VALUE}; 0 selects a free port).")
    private int port = 8765;

    @CommandLine.Option(names = "--no-browser", description = "Do not open the browser automatically.")
    private boolean noBrowser;

    @CommandLine.Option(names = "--check", description = "Check installation and configuration, then exit.")
    private boolean check;

    @Override
    public Integer call() throws ExecutionException {
        Path script = resolveConnector();
        String pythonCommand = python;
        if (pythonCommand == null || pythonCommand.isBlank()) {
            pythonCommand = System.getenv().getOrDefault("PYTHON", "python3");
        }

        List<String> args = new ArrayList<>();
        args.add(pythonCommand);
        args.add(script.toString());
        args.add("--repo");
        args.add(repository.toAbsolutePath().normalize().toString());
        args.add("--host");
        args.add(host);
        args.add("--port");
        args.add(Integer.toString(port));
        args.add("--citydb-command");
        args.add(citydbCommand);
        addPath(args, "--config", config);
        if (citygml != null) {
            args.add("--citygml");
            args.add(citygml.toString());
        }
        addPath(args, "--export-file", exportFile);
        if (noBrowser) {
            args.add("--no-browser");
        }
        if (check) {
            args.add("--check");
        }

        try {
            Process process = new ProcessBuilder(args)
                    .directory(repository.toAbsolutePath().normalize().toFile())
                    .inheritIO()
                    .start();
            return process.waitFor();
        } catch (Exception exception) {
            if (exception instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            throw new ExecutionException("Failed to start the 4dcitygml sync connector.", exception);
        }
    }

    private void addPath(List<String> args, String option, Path value) {
        if (value != null) {
            args.add(option);
            args.add(value.toAbsolutePath().normalize().toString());
        }
    }

    private Path resolveConnector() throws ExecutionException {
        if (connector != null) {
            return requireConnector(connector);
        }
        String configured = System.getenv("FOURDCITYGML_CONNECTOR");
        if (configured != null && !configured.isBlank()) {
            return requireConnector(Path.of(configured));
        }
        try {
            URI location = SyncCommand.class.getProtectionDomain().getCodeSource().getLocation().toURI();
            Path jar = Path.of(location).toAbsolutePath().normalize();
            Path root = Files.isRegularFile(jar) ? jar.getParent() : jar;
            if (root != null && root.getFileName() != null && root.getFileName().toString().equals("lib")) {
                root = root.getParent();
            }
            if (root != null) {
                return requireConnector(root.resolve("connector/server.py"));
            }
        } catch (Exception exception) {
            throw new ExecutionException("Failed to locate the bundled connector.", exception);
        }
        throw new ExecutionException("Connector not found. Use --connector or FOURDCITYGML_CONNECTOR.");
    }

    private Path requireConnector(Path value) throws ExecutionException {
        Path resolved = value.toAbsolutePath().normalize();
        if (!Files.isRegularFile(resolved)) {
            throw new ExecutionException("Connector not found: " + resolved);
        }
        return resolved;
    }
}
