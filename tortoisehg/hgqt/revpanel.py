# revpanel.py - TortoiseHg rev panel widget
#
# Copyright (C) 2007-2010 Logilab. All rights reserved.
# Copyright (C) 2010 Adrian Buehlmann <adrian@cadifra.com>
# Copyright (C) 2026 Peter Demcak <majster64@gmail.com> (dark theme)
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

from mercurial import (
    cmdutil,
    error,
)

from ..util import hglib, obsoleteutil
from ..util.i18n import _
from . import (
    csinfo,
    qtlib,
)

from .theme import THEME

def label_func(widget, item, ctx):
    if item == 'cset':
        if isinstance(ctx.rev(), str):
            return _('Patch:')
        return _('Changeset:')
    elif item == 'parents':
        return _('Parent:')
    elif item == 'children':
        return _('Child:')
    elif item == 'predecessors':
        return _('Predecessors:')
    elif item == 'successors':
        return _('Successors:')
    raise csinfo.UnknownItem(item)

def revid_markup(revid, **kargs):
    opts = dict(family='monospace', size='9pt')
    opts.update(kargs)
    return qtlib.markup(revid, **opts)

def data_func(widget, item, ctx):
    def summary_line(desc):
        return hglib.longsummary(desc.replace(b'\0', b''))
    def revline_data(ctx, hl=False, branch=None):
        if hglib.isbasestring(ctx):
            return ctx
        desc = ctx.description()
        rev = str(ctx.rev()) if isinstance(ctx.rev(), int) else hglib.tounicode(ctx.rev())
        return (rev, str(ctx), summary_line(desc), hl,
                hglib.tounicode(branch))
    def format_ctxlist(ctxlist):
        if not ctxlist:
            return None
        return [revline_data(ctx)[:3] for ctx in ctxlist]
    if item == 'cset':
        return revline_data(ctx)
    elif item == 'branch':
        value = hglib.tounicode(ctx.branch())
        return value != 'default' and value or None
    elif item == 'topic':
        topic = getattr(ctx, 'topic', lambda: None)()
        return topic and hglib.tounicode(topic) or None
    elif item == 'parents':
        # TODO: need to put 'diff to other' checkbox
        #pindex = self.diff_other_parent() and 1 or 0
        pindex = 0 # always show diff with first parent
        pctxs = ctx.parents()
        parents = []
        for pctx in pctxs:
            highlight = len(pctxs) == 2 and pctx == pctxs[pindex]
            branch = None
            if hasattr(pctx, 'branch') and pctx.branch() != ctx.branch():
                branch = pctx.branch()
            parents.append(revline_data(pctx, highlight, branch))
        return parents
    elif item == 'children':
        children = []
        for cctx in ctx.children():
            branch = None
            if hasattr(cctx, 'branch') and cctx.branch() != ctx.branch():
                branch = cctx.branch()
            children.append(revline_data(cctx, branch=branch))
        return children
    elif item in ('graft', 'transplant', 'mqoriginalparent',
                  'p4', 'svn', 'converted',):
        ts = widget.get_data(item, usepreset=True)
        if not ts:
            return None
        try:
            tctx = hglib.revsymbol(ctx.repo(), hglib.fromunicode(ts))
            return revline_data(tctx)
        except (error.LookupError, error.RepoLookupError, error.RepoError):
            return ts
    elif item == 'createsnewhead':
        # Strictly speaking, amend can create a new head in the case when
        # amending a revision which is not a topological head, as then the
        # original amended revision is kept alive by its orphan ancestors.
        # However, as the original amended revision along with its orphan
        # ancestors are eventually going to be evolved, we should not warn the
        # user. Instead, we should show that the amend will create orphans.
        return not widget.custom['isAmend'] and hglib.createsnewhead(ctx)
    elif item == 'createsorphans':
        return widget.custom['isAmend'] and any(p.children()
                                                for p in ctx.parents())
    elif item == 'reopensbranchhead':
        return any(p.closesbranch() and p.branch() == ctx.branch()
                   for p in ctx.parents())
    elif item == 'predecessors':
        ctxlist = obsoleteutil.first_known_predecessors(ctx)
        return format_ctxlist(ctxlist)
    elif item == 'successors':
        ctxlist = obsoleteutil.first_known_successors(ctx)
        return format_ctxlist(ctxlist)

    raise csinfo.UnknownItem(item)

def create_markup_func(ui):
    def link_markup(revnum, revid, linkpattern=None, ctx=None):
        mrevid = revid_markup('%s (%s)' % (revnum, revid))
        if linkpattern is None:
            return mrevid

        if linkpattern == b'cset:{node|short}':
            # this is the linkpattern for thg internal hyperlinks
            href = 'cset:%s' % revid
        else:
            if ctx is None:
                return mrevid
            try:
                # evaluates a generic mercurial template for changeset.link
                href = hglib.tounicode(cmdutil.rendertemplate(ctx, linkpattern))
            except (error.Abort, error.ParseError):
                return mrevid

        return '<a href="%s">%s</a>' % (qtlib.htmlescape(href), mrevid)

    def revline_markup(revnum, revid, summary, highlight=None,
                       branch=None, linkpattern=b'cset:{node|short}', ctx=None):
        def branch_markup(branch):
            opts = dict(fg='black', bg='#aaffaa')

            return qtlib.markup(' %s ' % branch, **opts)

        summary = qtlib.markup(summary)
        if branch:
            branch = branch_markup(branch)
        if revid:
            rev = link_markup(revnum, revid, linkpattern=linkpattern, ctx=ctx)
            if branch:
                return '%s %s %s' % (rev, branch, summary)
            return '%s %s' % (rev, summary)
        else:
            revnum = qtlib.markup(revnum)
            if branch:
                return '%s - %s %s' % (revnum, branch, summary)
            return '%s - %s' % (revnum, summary)

    def markup_func(widget, item, value):
        if item in ('cset', 'graft', 'transplant', 'mqoriginalparent',
                    'p4', 'svn', 'converted'):
            if item == 'cset':
                linkpattern = ui.config(b'tortoisehg', b'changeset.link')
            else:
                linkpattern = b'cset:{node|short}'
            if hglib.isbasestring(value):
                return revid_markup(value)
            return revline_markup(linkpattern=linkpattern, *value, ctx=widget.ctx)
        elif item in ('parents', 'children', 'predecessors', 'successors'):
            csets = []
            for cset in value:
                if hglib.isbasestring(cset):
                    csets.append(revid_markup(cset))
                else:
                    csets.append(revline_markup(*cset))
            return csets
        raise csinfo.UnknownItem(item)

    return markup_func

def RevPanelWidget(repo):
    '''creates a rev panel widget and returns it'''
    custom = csinfo.custom(data=data_func, label=label_func,
                           markup=create_markup_func(repo.ui))
    style = csinfo.panelstyle(contents=(
                   'cset', 'gitcommit', 'branch', 'topic', 'obsolete', 'close',
                   'user',
                   'dateage', 'parents', 'children', 'tags', 'graft', 'transplant',
                   'mqoriginalparent',
                   'predecessors', 'successors',
                   'p4', 'svn', 'converted'), selectable=True,
                   expandable=True)
    return csinfo.create(repo, style=style, custom=custom)


def nomarkup(widget, item, value):
    def revline_markup(revnum, revid, summary, highlight=None, branch=None):
        summary = qtlib.markup(summary)
        if revid:
            rev = revid_markup('%s (%s)' % (revnum, revid))
            return '%s %s' % (rev, summary)
        else:
            revnum = qtlib.markup(revnum)
            return '%s - %s' % (revnum, summary)
    csets = []
    if item == 'createsnewhead':
        if value is True:
            text = _('Creates new head!')
            if THEME.enabled:
                return qtlib.markup(text,
                                    fg=THEME.warning_text.name(), 
                                    bg=THEME.warning_background.name(),
                                    weight='bold')
            else:
                return qtlib.markup(text, fg='red', weight='bold')
        raise csinfo.UnknownItem(item)
    elif item == 'createsorphans':
        if value is True:
            text = _('Creates orphans.')
            return qtlib.markup(text, weight='bold')
        raise csinfo.UnknownItem(item)
    elif item == 'reopensbranchhead':
        if value is True:
            text = _('Reopens closed branch head!')
            if THEME.enabled:
                return qtlib.markup(text,
                                    fg=THEME.warning_text.name(), 
                                    bg=THEME.warning_background.name(),
                                    weight='bold')
            else:
                return qtlib.markup(text, fg='red', weight='bold')
        raise csinfo.UnknownItem(item)
    for cset in value:
        if hglib.isbasestring(cset):
            csets.append(revid_markup(cset))
        else:
            csets.append(revline_markup(*cset))
    return csets

def WDirInfoWidget(repo):
    'creates a wdir info widget and returns it'
    custom = csinfo.custom(data=data_func, label=label_func, markup=nomarkup)
    style = csinfo.panelstyle(contents=('parents', 'createsnewhead',
                                        'createsorphans', 'reopensbranchhead'),
                              selectable=True)
    return csinfo.create(repo, style=style, custom=custom)
