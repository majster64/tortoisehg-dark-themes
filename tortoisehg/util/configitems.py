# configitems.py - declaration of TortoiseHg configurations
#
# Copyright 2018 Yuya Nishihara <yuya@tcha.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

from mercurial import (
    registrar,
)

from tortoisehg.util import (
    hgversion,
)

configtable = {}
configitem = registrar.configitem(configtable)

# special value for case where the default is derived from other values
dynamicdefault = configitem.dynamicdefault  # pytype: disable=attribute-error

testedwith = hgversion.testedwith

configitem(b'experimental', b'graph-group-branches', default=False)
configitem(b'experimental', b'graph-group-branches.firstbranch', default=b'')
configitem(b'experimental', b'thg.fix-app-font', default=True)
configitem(b'experimental', b'thg.displaynames', default=None)
configitem(b'reviewboard', b'browser', default=None)
configitem(b'reviewboard', b'password', default=None)
configitem(b'reviewboard', b'repoid', default=None)
configitem(b'reviewboard', b'server', default=None)
configitem(b'reviewboard', b'user', default=None)
configitem(b'tortoisehg', b'activatebookmarks', default=b'prompt')
configitem(b'tortoisehg', b'authorcolor', default=False)
configitem(b'tortoisehg', b'autoinc', default=b'')
configitem(b'tortoisehg', b'autoresolve', default=True)
configitem(b'tortoisehg', b'branchcolors', default=None)
configitem(b'tortoisehg', b'changeset.link', default=None)
configitem(b'tortoisehg', b'ciexclude', default=b'')
configitem(b'tortoisehg', b'cipushafter', default=None)
configitem(b'tortoisehg', b'closeci', default=False)
configitem(b'tortoisehg', b'cmdserver.readtimeout', default=30)
configitem(b'tortoisehg', b'confirmaddfiles', default=True)
configitem(b'tortoisehg', b'confirmdeletefiles', default=True)
configitem(b'tortoisehg', b'confirmpush', default=True)
configitem(b'tortoisehg', b'copy_hash_selection', default=False)
configitem(b'tortoisehg', b'deadbranch', default=b'')
configitem(b'tortoisehg', b'defaultclonedest', default=None)
configitem(b'tortoisehg', b'defaultpush', default=b'all')
configitem(b'tortoisehg', b'defaultwidget', default=None)
configitem(b'tortoisehg', b'editor', default=None)
configitem(b'tortoisehg', b'engmsg', default=False)
configitem(b'tortoisehg', b'fontcomment', default=dynamicdefault)
configitem(b'tortoisehg', b'fontdiff', default=dynamicdefault)
configitem(b'tortoisehg', b'fonteditor', default=dynamicdefault)
configitem(b'tortoisehg', b'fontlog', default=dynamicdefault)
configitem(b'tortoisehg', b'fontoutputlog', default=dynamicdefault)
configitem(b'tortoisehg', b'forcerepotab', default=False)
configitem(b'tortoisehg', b'forcevdiffwin', default=False)
configitem(b'tortoisehg', b'fullauthorname', default=False)
configitem(b'tortoisehg', b'fullpath', default=False)
configitem(b'tortoisehg', b'graphlimit', default=500)
configitem(b'tortoisehg', b'graphopt', default=False)
configitem(b'tortoisehg', b'guifork', default=None)
configitem(b'tortoisehg', b'hidetags', default=b'')
configitem(b'tortoisehg', b'immediate', default=b'')
configitem(b'tortoisehg', b'initialrevision', default=b'current')
configitem(b'tortoisehg', b'initskel', default=None)
configitem(b'tortoisehg', b'issue.bugtraqparameters', default=None)
configitem(b'tortoisehg', b'issue.bugtraqplugin', default=None)
configitem(b'tortoisehg', b'issue.bugtraqtrigger', default=None)
configitem(b'tortoisehg', b'issue.inlinetags', default=False)
configitem(b'tortoisehg', b'issue.link', default=None)
configitem(b'tortoisehg', b'issue.linkmandatory', default=False)
configitem(b'tortoisehg', b'issue.regex', default=None)
configitem(b'tortoisehg', b'longsummary', default=False)
configitem(b'tortoisehg', b'maxdiff', default=None)
configitem(b'tortoisehg', b'mergecommitmessage', default=None)
configitem(b'tortoisehg', b'monitorrepo', default=b'localonly')
configitem(b'tortoisehg', b'opentabsaftercurrent', default=True)
configitem(b'tortoisehg', b'postpull', default=None)
configitem(b'tortoisehg', b'promoteditems', default=b'commit,log')
configitem(b'tortoisehg', b'readme', default=None)
configitem(b'tortoisehg', b'recurseinsubrepos', default=None)
configitem(b'tortoisehg', b'refreshwdstatus', default=b'auto')
configitem(b'tortoisehg', b'shell', default=None)
configitem(b'tortoisehg', b'show-branch-head-label', default=True)
configitem(b'tortoisehg', b'showfamilyline', default=True)
configitem(b'tortoisehg', b'summarylen', default=None)
configitem(b'tortoisehg', b'tabwidth', default=None)
configitem(b'tortoisehg', b'tasktabs', default=b'off')
configitem(b'tortoisehg', b'traceback', default=False)
configitem(b'tortoisehg', b'ui.language', default=None)
configitem(b'tortoisehg', b'vdiff', default=None)
configitem(b'tortoisehg', b'workbench.commit.custom-menu', default=list)
configitem(b'tortoisehg', b'workbench.custom-toolbar', default=list)
configitem(b'tortoisehg', b'workbench.filelist.custom-menu', default=list)
configitem(b'tortoisehg', b'workbench.multipleselection.custom-menu',
           default=list)
configitem(b'tortoisehg', b'workbench.pairselection.custom-menu', default=list)
configitem(b'tortoisehg', b'workbench.revdetails.custom-menu', default=list)
configitem(b'tortoisehg', b'workbench.single', default=True)
configitem(b'tortoisehg', b'workbench.target-combo', default=b'auto')
configitem(b'tortoisehg', b'workbench.task-toolbar', default=list)
