Ext.ns('Deluge.ux');

Deluge.ux.DelugramWindowBase = Ext.extend(Ext.Window, {
    layout: 'fit',
    width: 400,
    height: 130,
    closeAction: 'hide',

    initComponent: function () {
        Deluge.ux.DelugramWindowBase.superclass.initComponent.call(this);
        this.addButton(_('Cancel'), this.onCancelClick, this);

        this.form = this.add({
            xtype: 'form',
            baseCls: 'x-plain',
            bodyStyle: 'padding: 5px',
            items: [
                {
                    xtype: 'textfield',
                    fieldLabel: _('User ID'),
                    name: 'user_id',
                    width: 270,
                },
                {
                    xtype: 'textfield',
                    fieldLabel: _('Name'),
                    name: 'name',
                    width: 270,
                },
            ],
        });
    },

    onCancelClick: function () {
        this.hide();
    },
});

Deluge.ux.AddDelugramUserWindow = Ext.extend(Deluge.ux.DelugramWindowBase, {
    title: _('Add User'),

    initComponent: function () {
        Deluge.ux.AddDelugramUserWindow.superclass.initComponent.call(this);
        this.addButton(_('Add'), this.onAddClick, this);
        this.addEvents({
            useradd: true,
        });
    },

    onAddClick: function () {
        var values = this.form.getForm().getFieldValues();
        deluge.client.delugram.add_user(values.user_id, values.name, {
            success: function () {
                this.fireEvent(
                    'useradd',
                    this,
                    values.user_id,
                    values.name
                );
            },
            scope: this,
        });
        this.hide();
    },
});

Ext.ns('Deluge.ux.preferences');

/**
 * @class Deluge.ux.preferences.DelugramPage
 * @extends Ext.Panel
 */
Deluge.ux.preferences.DelugramPage = Ext.extend(Ext.Panel, {
    title: _('Delugram'),
    header: true,
    layout: 'fit',
    border: false,

    initComponent: function () {
        Deluge.ux.preferences.DelugramPage.superclass.initComponent.call(this);

        this.form = this.add({
            xtype: 'form',
            baseCls: 'x-plain',
            bodyStyle: 'padding: 5px',
            autoHeight: true,
            items: [
                {
                    xtype: 'textfield',
                    defaultType: 'textfield',
                    fieldLabel: _('Telegram Token'),
                    name: 'telegram_token',
                    width: 225,
                },
                {
                    xtype: 'textfield',
                    defaultType: 'textfield',
                    fieldLabel: _('Admin Chat ID'),
                    name: 'admin_chat_id',
                    width: 225,
                },
                {
                    xtype: 'button',
                    text: _('Save'),
                    handler: this.onSaveClick,
                    scope: this,
                },
            ],
        });

        this.users_list = new Ext.list.ListView({
            store: new Ext.data.JsonStore({
                fields: [
                    'user_id',
                    'name'
                ],
            }),
            columns: [
                {
                    id: 'user_id',
                    width: 0.3,
                    header: _('User ID'),
                    sortable: true,
                    dataIndex: 'user_id',
                },
                {
                    id: 'name',
                    header: _('Name'),
                    sortable: true,
                    dataIndex: 'name',
                },
            ],
            singleSelect: true,
            autoExpandColumn: 'name',
            emptyText: 'No users',
        });
        this.users_list.on('selectionchange', this.onSelectionChange, this);

        this.panel = this.add({
            items: [
                this.users_list,
            ],
            bbar: {
                items: [
                    {
                        text: _('Add'),
                        iconCls: 'icon-add',
                        handler: this.onAddClick,
                        scope: this,
                    },
                    {
                        text: _('Remove'),
                        iconCls: 'icon-remove',
                        handler: this.onRemoveClick,
                        scope: this,
                        disabled: true,
                    },
                ],
            },
        });

        this.on('show', this.onPreferencesShow, this);
    },

    reloadConfig: function () {
        deluge.client.delugram.get_config({
            success: function (config) {
                this.users_list.getStore().loadData(config.users);
                this.form.getForm().setValues({
                    telegram_token: config.telegram_token,
                    admin_chat_id: config.admin_chat_id,
                });
            },
            scope: this,
        });
    },

    onSaveClick: function () {
        var values = this.form.getForm().getFieldValues();
        deluge.client.delugram.set_config(values, {
            success: function () {
                Ext.Msg.alert(_('Success'), _('Configuration saved'));
                this.reloadConfig();
            },
            scope: this,
        });
    },

    onAddClick: function () {
        if (!this.addWin) {
            this.addWin = new Deluge.ux.AddDelugramUserWindow();
            this.addWin.on(
                'useradd',
                function () {
                    this.reloadConfig();
                },
                this
            );
        }
        this.addWin.show();
    },

    onPreferencesShow: function () {
        this.reloadConfig();
    },

    onRemoveClick: function () {
        var record = this.users_list.getSelectedRecords()[0];
        deluge.client.delugram.remove_user(record.json.user_id, {
            success: function () {
                this.reloadConfig();
            },
            scope: this,
        });
    },

    onSelectionChange: function (dv, selections) {
        if (selections.length) {
            this.panel.getBottomToolbar().items.get(1).enable();
        } else {
            this.panel.getBottomToolbar().items.get(1).disable();
        }
    },
});

Deluge.plugins.DelugramPlugin = Ext.extend(Deluge.Plugin, {
    name: 'Delugram',

    onDisable: function () {
        console.log("Delugram plugin disabled");
        deluge.preferences.removePage(this.prefsPage);
    },

    onEnable: function () {
        console.log("Delugram plugin enabled");
        this.prefsPage = deluge.preferences.addPage(
            new Deluge.ux.preferences.DelugramPage()
        );
    },
});
Deluge.registerPlugin('Delugram', Deluge.plugins.DelugramPlugin);