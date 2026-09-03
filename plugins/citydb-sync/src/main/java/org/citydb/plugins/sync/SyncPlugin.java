/*
 * Copyright (c) 2026 4dcitygml
 * SPDX-License-Identifier: Apache-2.0
 */
package org.citydb.plugins.sync;

import org.citydb.plugin.Extension;
import org.citydb.plugin.Plugin;

import java.util.List;

public final class SyncPlugin extends Plugin {
    @Override
    public List<Class<? extends Extension>> getExtensions() {
        return List.of(SyncCommand.class);
    }
}
