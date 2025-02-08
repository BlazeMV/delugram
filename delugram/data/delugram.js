/**
 * Script: delugram.js
 *     The client-side javascript code for the delugram plugin.
 *
 * Copyright:
 *     (C) BlazeMv 2025 <ad.adamdavid72@gmail.com>
 *
 *     This file is part of delugram and is licensed under GNU GPL 3.0, or
 *     later, with the additional special exception to link portions of this
 *     program with the OpenSSL library. See LICENSE for more details.
 */

delugramPlugin = Ext.extend(Deluge.Plugin, {
    constructor: function(config) {
        config = Ext.apply({
            name: 'delugram'
        }, config);
        delugramPlugin.superclass.constructor.call(this, config);
    },

    onDisable: function() {
        deluge.preferences.removePage(this.prefsPage);
    },

    onEnable: function() {
        this.prefsPage = deluge.preferences.addPage(
            new Deluge.ux.preferences.delugramPage());
    }
});
new delugramPlugin();
