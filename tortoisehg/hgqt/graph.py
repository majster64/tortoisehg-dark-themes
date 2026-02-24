# graph.py - helper functions and classes to ease hg revision graph building
#
# Copyright (c) 2003-2010 LOGILAB S.A. (Paris, FRANCE).
# http://www.logilab.fr/ -- mailto:contact@logilab.fr
# Copyright (C) 2026 Peter Demcak <majster64@gmail.com> (dark theme)
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation; either version 2 of the License, or (at your option) any later
# version.

"""helper functions and classes to ease hg revision graph building

Based on graphlog's algorithm, with inspiration stolen from TortoiseHg
revision grapher (now stolen back).

The primary interface are the *_grapher functions, which are generators
of Graph instances that describe a revision set graph. These generators
are used by repomodel.py which renders them on a widget.
"""

from __future__ import annotations

import collections
import os
import time
import typing

from mercurial.thirdparty import attr

# Force pytype to use the non-vendored package
if typing.TYPE_CHECKING:
    # noinspection PyPackageRequirements
    import attr

from mercurial import (
    error,
    hg,
    node,
    phases,
    pycompat,
    revset as revsetmod,
)
from mercurial.utils import (
    urlutil,
)

from ..util import (
    obsoleteutil,
)

from tortoisehg.hgqt.theme import THEME

r"""
How each edge color is determined
=================================

Legends
-------
o, 0, 1, 2, ..., 9
    visible revision
x
    hidden revision
`|a`, `a|` ("a" can be a-z)
    graph edge.
    edges with same alphabet have same color

Rules
-----

A. Edges on the same first-ancestors-line have same color

.. code::

    o
    |a
    o
    |a
    o

B. Edges on branched-merged line have different color from base line

.. code::

    o               o
   a|               |\b
    o               | o
   a|\b            a| |\c
    | o             | | \
    o |b            | |  o
   a| o             o |b |
    |/b            a| | /c
    o               | |/
   a|               | o
    o               o |b
                   a| o
                    |/b
                    o
                   a|
                    o

C. Merged edge has same color as merged-from line

.. code::

   9
   |\    all merged lines(1-3, 4-6, 7-9) and right line(0-1-4-7-9) have
   8 |   same color
   | 7
   6 |
   |\|
   5 |
   | 4
   3 |
   |\|
   2 |
   | 1
   |/
   0

D. Edges on the same first-ancestors-line have same color even if
   separated by revset

.. code::

    4
   a|   Sometimes graph is separated into several parts by revset filter.
    3
    :   All edges on the same first-ancestors-line have same color,
    x   even if they are separated by filter.
    :
    1
   a|
    0

E. Grafted line has different color from source, destination, and
   other grafted lines

.. code::

    5
    |\
   a| \    1-4 and 2-5 are grafted line
    4  \
   a|\c :d
    3 : :
    | : 2
   a| :/b
    | 1
    |/b
    0

Family line implementation
==========================
Terms
-----
Edge
  line which connect two revisions directly

Path
  unbranched line which connect two revisions directly or indirectly.
  (Intermediate revisions can exist on the path)

Parent line
  Edge between revision and its direct parent

Family line
  Extension edge to complete revision depencency on the filtered graph.

Next visible ancestor(s)
  Next visible ancestors of rev.X means the ancestor revisions that are
  neighboring with rev.X when ignoring hidden revisions.

Description
-----------
In the filtered dag with family line support, we must show at least one path
between any visible revision and any ancestor of it.

Examples
--------
Legends
~~~~~~~
o, 0, 1, 2, ..., 9
    visible revision
x
    hidden revision
`|<`
    family line

Simple cases
~~~~~~~~~~~~

.. code::

    ALL  FILTERED      ALL    FILTERED       ALL    FILTERED
     3      3           4        4            4        4
     |      |<          |\       |\<          |\       |\
     x  ->  |<          | x      | |<         | 3      | 3
     |      |<          | |  ->  | |<         | |  ->  | |<
     1      1           | 2      | 2          | x      | |<
                        |/       |/           |/       |/<
                        1        1            1        1
Advanced cases
~~~~~~~~~~~~~~

..code::

    ALL       FILTERED
     3         3
     |\        |        No family line is drawn at 3-1
     | x       |        because there is already parent line.
     |/        |
     1         1

    ALL       FILTERED
     6           6
     |\          |      1 and 3 are next visible ancestors of 6,
     5 |         5      but no family lines are drawn at 6-3 and 6-1,
     | x         |      because 6-3 and 6-1 path already exist (6-5-3-1)
     |/|    ->   |
     3 |         3
     | x         |
     |/          |
     1           1

    ALL       FILTERED
     5           5
     |\          |<     Both 1 and 2 are next visible ancestors of 5,
     x |         |<     but no family line is drawn at 5-1 because 5-2 edge
     | x   ->    |<     completes 5-1 path at the same time.
     2 |         2
     |/          |
     1           1

Given such cases, we can determine family line location as below:

    If Rev-X and Rev-Y (X > Y) meets these all conditions, family line
    will be drawn between X and Y.

    1. X and Y are both visible (not hidden)
    2. Y is ancestor of X
    3. Revisions in DAG between X and Y are all hidden
    4. Y is *NOT* ancestor of visible parents of X
    5. Y is *NOT* ancestor of any other lower-end revisions of family line
       from X
"""

LINE_TYPE_PARENT = 0
LINE_TYPE_FAMILY = 1
LINE_TYPE_GRAFT = 2
LINE_TYPE_OBSOLETE = 3

NODE_SHAPE_REVISION = 0
NODE_SHAPE_CLOSEDBRANCH = 1
NODE_SHAPE_APPLIEDPATCH = 2
NODE_SHAPE_UNAPPLIEDPATCH = 3
NODE_SHAPE_REVISION_DRAFT = 4
NODE_SHAPE_REVISION_SECRET = 5

if THEME.enabled:
    # Dark-theme branch / graph colors
    COLORS = [
        '#3a5dc4',  # dark blue
        '#14532d',  # dark green
        '#166534',  # green (darkened)
        '#1e3a8a',  # dark blue
        '#4c1d95',  # purple
        '#1e40af',  # dodger blue (dark)
        '#5f5a1a',  # dark yellow / olive
        '#86198f',  # magenta (dark)
        '#5b1366',  # dark magenta
        '#155e75',  # dark cyan
    ]
else:
    # TODO: Remove these two when we adopt GTK author color scheme
    COLORS = [
        '#0000ff',  # blue
        '#006400',  # dark green
        '#008000',  # green
        '#00008b',  # dark blue
        '#800080',  # purple
        '#1e90ff',  # dodger blue
        '#808000',  # dark yellow
        '#ff00ff',  # magenta
        '#8b008b',  # dark magenta
        '#008b8b',  # dark cyan
    ]

def hashcolor(data, modulo=None):
    """function to reliably map a string to a color index

    The algorithm used is very basic and can be improved if needed.
    """
    if modulo is None:
        modulo = len(COLORS)
    if isinstance(data, (str, pycompat.unicode)):
        idx = sum([ord(c) for c in data])
    else: # Python 3 bytes
        idx = sum(data)
    idx %= modulo
    return idx


class StandardDag:
    """Generate DAG for grapher

    Public fields:
        repo        The repository
        start_rev   Tip-most revision of range to graph
                    This can be None, which means workingtree
        stop_rev    0-most revision of range to graph
        branch      If set, then only revisions in this branch only iterated.
        allparents  If set in addition to branch, then cset outside the
                    branch that are ancestors to some cset inside the branch
                    is also iterated
        showgraftsource
                    If set, return graft relations additionally
        visiblerev  The function to determine revision visiblity,
                    which accepts one argument(revno) and return bool value
                    (True if visible)

    walk() iterates visible nodes with this form (ctx is changectx or filectx):
        `(ctx, [(parent ctx, line type, p1 or not), ...])`
    """
    def __init__(self, repo, start_rev, stop_rev, branch, allparents,
                 showgraftsource, visiblerev):
        assert start_rev is None or start_rev >= stop_rev, (start_rev, stop_rev)
        self.repo = repo
        self.start_rev = start_rev
        self.stop_rev = stop_rev
        self.branch = branch
        self.allparents = allparents
        self.showgraftsource = showgraftsource
        self.visiblerev = visiblerev
        if self.allparents or not branch:
            def visiblectx(ctx):
                return bool(ctx)
        else:
            def visiblectx(ctx):
                return ctx and ctx.branch() == branch
        self.visiblectx = visiblectx

    def _iter_revs(self, repo, visiblerev):
        stop_rev = self.stop_rev
        curr_rev = self.start_rev
        if curr_rev is None:
            if visiblerev(curr_rev):
                yield repo[curr_rev]
            curr_rev = len(repo) - 1
        # jump in the branch grouping graph experiment if the user subscribed
        revs = revsetmod.spanset(repo, curr_rev, stop_rev - 1)
        if revs and repo.ui.configbool(b'experimental', b'graph-group-branches'):
            start, stop = revs.first(), revs.last()
            revset = b'sort(%d:%d, "topo"'
            args = [start, stop]
            firstbranchrevset = repo.ui.config(
                b'experimental', b'graph-group-branches.firstbranch')
            if firstbranchrevset:
                revset += b', topo.firstbranch=%s'
                args.append(firstbranchrevset)
            revset += b')'
            revs = repo.revs(revset, *args)

        for curr_rev in revs:
            if visiblerev(curr_rev):
                yield repo[curr_rev]

    def _append_graft_source(self, ctx, parents):
        src_rev_str = ctx.extra().get(b'source')
        if src_rev_str is not None and src_rev_str in self.repo:
            src = self.repo[src_rev_str]
            src_rev = src.rev()
            if self.stop_rev <= src_rev < ctx.rev() and \
                    self.visiblerev(src_rev) and self.visiblectx(src):
                parents.append((src, LINE_TYPE_GRAFT, False))
        for octx in obsoleteutil.first_known_predecessors(ctx):
            src_rev = octx.rev()
            if self.stop_rev <= src_rev < ctx.rev() and \
                    self.visiblerev(src_rev) and self.visiblectx(octx):
                parents.append((octx, LINE_TYPE_OBSOLETE, False))

    def walk(self):
        repo = self.repo
        branch = self.branch
        showgraftsource = self.showgraftsource
        visiblerev = self.visiblerev
        visiblectx = self.visiblectx

        upcomingparents = set()
        for ctx in self._iter_revs(repo, visiblerev):
            if ctx.rev() not in upcomingparents:
                if branch and ctx.branch() != branch:
                    continue
            else:
                upcomingparents.remove(ctx.rev())

            parents = [(p, LINE_TYPE_PARENT, i == 0)
                       for i, p in enumerate(filter(visiblectx, ctx.parents()))
                       if visiblerev(p.rev())]
            if showgraftsource:
                self._append_graft_source(ctx, parents)

            upcomingparents.update([p[0].rev() for p in parents])

            yield ctx, parents


class _FamilyLineRev:
    r"""Revision information for building family line relations

    Public fields:
        rev     Revision number. Can be None (means workingdir)
        visible True if self should be shown
        destinations
                List of parent/family line edge destinations.
                Each elements are tuple:
                    revno       revision number of edge destination edge
                    linetype    LINE_TYPE_PARENT or LINE_TYPE_FAMILY
                    is_p1       True if revno is in ancestors(p1(self.rev))
        next_descendants
                dictionary:
                key     _FamilyLineRev which can be upper-end of family line
                        edge to self.rev
                value   True if self.rev is in ancestors(p1(key.rev))
        excluded_descendants
                frozenset of _FamilyLineRev.
                Revisions which are excluded from next_descendants.
                family line is *NOT* drawn between self and these revisions.
        pending
                Number of unclosed edges of which upper-end is self.rev
                Initial value is number of hidden parents(set by proceed()),
                and incremented or decremented with proceeding DAG scan.
                It will become 0 when all NVAs of self are determined.

    This is illustration of relations between instances

      :           +--------------------------------------+
      o           |        upper visible revision        |
      |           +--------------------------------------+
      x          next_descendants ^  | destinations    ^
      |\                          |  v (FAMILY)        |
      | |                    +--------------+          |
      @ |                    |     self     |          | next_descendants
      | |                    +--------------+          | excluded_descendants
      | x    excluded_descendants ^  | destinations    |
      | |                    (*1) |  v (PARENT)        |
      |/          +--------------------------------------+
      o           |       lower visible revision         |
      :           +--------------------------------------+

        (*1) because here is parent line, not family line
    """
    __slots__ = ["rev", "visible", "next_descendants", "excluded_descendants",
                 "pending", "destinations"]

    def __init__(self, rev, visible):
        self.rev = rev
        self.visible = visible
        self.next_descendants = {}
        self.excluded_descendants = set()
        self.pending = 0
        self.destinations = []

    def proceed(self, parents):
        next_descendants = self.next_descendants
        excluded_descendants = self.excluded_descendants
        excluded_descendants.difference_update([r for r in excluded_descendants
                                                if not r.pending])

        # decrement `pending` of each next_descendants regardless of
        # self.visible once.
        # (it will be reincremented if self is hidden and self has parents)
        for nd in next_descendants:
            nd.pending -= 1
            assert nd.pending >= 0

        if excluded_descendants:
            next_descendants = dict(kv for kv in next_descendants.items()
                                    if kv[0] not in excluded_descendants)

        if self.visible:
            for nd, is_p1 in next_descendants.items():
                nd.destinations.append((self.rev, LINE_TYPE_FAMILY, is_p1))

            # `next_descendants` are also excluded from next_descendants
            # of parents because of definition #4
            parent_ed = excluded_descendants.union(next_descendants)
            for i, p in enumerate(parents):
                p.add_excluded_descendants(parent_ed)
                if p.visible:
                    self.destinations.append((p.rev, LINE_TYPE_PARENT, i == 0))
                    p.add_excluded_descendants([self])
                else:
                    p.add_next_descendants({self: i == 0})
        else:
            # just pass to parents
            for p in parents:
                p.add_next_descendants(next_descendants)
                p.add_excluded_descendants(excluded_descendants)

        # these are no longer needed
        self.next_descendants = self.excluded_descendants = None

    def add_next_descendants(self, descendants):
        for d, is_p1 in descendants.items():
            if d in self.next_descendants:
                self.next_descendants[d] |= is_p1
            else:
                d.pending += 1
                self.next_descendants[d] = is_p1

    def add_excluded_descendants(self, descendants):
        self.excluded_descendants.update(descendants)

    def __hash__(self):
        return hash(self.rev)

    def __eq__(self, other):
        return isinstance(other, _FamilyLineRev) and self.rev == other.rev

    def __ne__(self, other):
        return not self.__eq__(other)

    def __repr__(self):
        if self.rev is None:
            return "_FamilyLineRev(+)"
        else:
            return "_FamilyLineRev(%d)" % self.rev


class FamilyLineDag(StandardDag):
    """Generate filtered DAG with family lines for grapher"""

    def walk(self):
        repo = self.repo
        stop_rev = self.stop_rev
        showgraftsource = self.showgraftsource
        upcomingrevs = {}
        visiblerev = self.visiblerev
        visiblectx = self.visiblectx

        def get_or_create_rev(ctx):
            rev = ctx.rev()
            ret = upcomingrevs.get(rev)
            if not ret:
                ret = upcomingrevs[rev] = \
                    _FamilyLineRev(rev, visiblerev(rev) and visiblectx(ctx))
            return ret

        queue = collections.deque()
        for ctx in self._iter_revs(repo, lambda rev: True):
            rev = upcomingrevs.pop(ctx.rev(), None)
            if not rev:
                if not visiblerev(ctx.rev()) or not visiblectx(ctx):
                    continue
                rev = _FamilyLineRev(ctx.rev(), True)

            parents = [get_or_create_rev(p) for p in ctx.parents()
                       if p.rev() >= stop_rev]
            rev.proceed(parents)
            if rev.visible:
                queue.append(rev)

            # yield after rev.pending becomes 0
            while queue and not queue[0].pending:
                r = queue.popleft()
                # order by p1 -> p2, small rev -> large rev
                destinations = sorted(r.destinations,
                                      key=lambda e: (not e[2], e[0]))
                parents = [(repo[pno], linktype, is_p1)
                           for (pno, linktype, is_p1) in destinations]
                rctx = repo[r.rev]
                if showgraftsource:
                    self._append_graft_source(rctx, parents)
                yield rctx, parents

        assert not queue, queue


def revision_grapher(repo, opts):
    """incremental revision grapher

    param repo       The repository
    opt   revset     set of revisions to graph.
    opt   branch     Only graph this branch
    opt   allparents If set in addition to branch, then cset outside the
                     branch that are ancestors to some cset inside the branch
                     is also graphed
    opt   showfamilyline
                     If set in addition to revset, then family line will be
                     shown between descendants and ancestors

    This generator function walks through the revision range in descending
    order.
    When revset is specified, range is from max(revset) to min(revset),
    otherwise from working tree(pseudo revision) to rev0.
    For each revision emits tuples with the following elements:

      - current revision
      - column of the current node in the set of ongoing edges
      - color of the node (?)
      - lines: a list of ((col, next_col), edge)
        defining the edges between the current row and the next row
      - parent revisions of current revision
    """
    revset = opts.get('revset')
    if revset:
        start_rev = max(revset)
        stop_rev = min(revset)
        visiblerev = lambda rev: rev in revset
    else:
        start_rev = None
        stop_rev = 0
        visiblerev = lambda rev: True
    if revset and opts.get('showfamilyline'):
        cls = FamilyLineDag
    else:
        cls = StandardDag

    dag = cls(repo, start_rev, stop_rev,
              opts.get('branch'), opts.get('allparents'),
              opts.get('showgraftsource'), visiblerev)
    return _iter_graphnodes(dag, GraphNode.fromchangectx)


def _iter_graphnodes(dag, nodefactory):
    revs = []
    activeedges = []  # order is not important

    rev_color = RevColorPalette()

    for ctx, parents in dag.walk():
        curr_rev = ctx.rev()
        # Compute revs and next_revs.
        if curr_rev not in revs:
            # New head.
            revs.append(curr_rev)
        rev_index = revs.index(curr_rev)
        next_revs = revs[:]
        activeedges = [e for e in activeedges if e.endrev < curr_rev]

        # Add parents to next_revs.
        parents_to_add = []
        for pctx, link_type, is_p1 in parents:
            parent = pctx.rev()
            if parent not in next_revs:
                # Because the parents originate from multiple sources, it is
                # theoretically possible that several point to the same
                # revision.  Only take the first of this (which is graftsource
                # because it is added before).
                if parent in parents_to_add:
                    continue
                parents_to_add.append(parent)
            if is_p1:
                color = rev_color[ctx]
            elif link_type in (LINE_TYPE_GRAFT, LINE_TYPE_OBSOLETE):
                color = rev_color.nextcolor()
            else:
                color = rev_color[pctx]
            activeedges.append(GraphEdge(curr_rev, parent, color, link_type))

        next_revs[rev_index:rev_index + 1] = parents_to_add

        lines = []
        for e in activeedges:
            if e.startrev == curr_rev:
                r = e.startrev
            else:
                r = e.endrev
            p = (revs.index(r), next_revs.index(e.endrev))
            lines.append((p, e))

        yield nodefactory(dag.repo, ctx, rev_index, lines)
        revs = next_revs


def filelog_grapher(repo, path):
    '''
    Graph the ancestry of a single file (log).  Deletions show
    up as breaks in the graph.
    '''

    if hasattr(repo, 'shallowmatch') and repo.shallowmatch(path):
        filedag = ShallowFileDag
    else:
        filedag = FileDag

    dag = filedag(repo, path)
    return _iter_graphnodes(dag, GraphNode.fromfilectx)


class FileDag:
    def __init__(self, repo, path):
        self.repo = repo
        self.path = path

    def _getflogheads(self):
        flog = self.repo.file(self.path)
        return flog.heads()

    def walk(self):
        flogheads = self._getflogheads()
        heads = sorted(self.repo.filectx(self.path, fileid=x).rev()
                       for x in flogheads) or [-1]

        rev = heads.pop()

        _paths = {}

        while rev >= 0:
            revpath = _paths.pop(rev, self.path)

            # Add parents to next_revs
            fctx = self.repo.filectx(revpath, changeid=rev)
            for pfctx in fctx.parents():
                _paths[pfctx.rev()] = pfctx.path()
            parents = [(pfctx, LINE_TYPE_PARENT, i == 0)
                       for i, pfctx in enumerate(fctx.parents())]

            yield fctx, parents

            if _paths:
                rev = max(_paths)
            else:
                rev = -1
            if heads and rev <= heads[-1]:
                rev = heads.pop()


class ShallowFileDag(FileDag):
    """FileDag specialization for shallow (remotefilelog) repository"""

    def _getflogheads(self):
        repo = self.repo
        path = self.path

        try:
            # ui.expandpath() was dropped in hg dcf983a5f906, and
            # urlutil.get_unique_pull_path_obj() was added in hg f4626b74b941.
            # Additionally, hg.peer() started taking a path object in hg
            # aa36771ef505.  All of this landed in hg 6.4.

            try:
                # pytype: disable=module-attr
                dest = urlutil.get_unique_pull_path_obj(
                    b'getflogheads', repo.ui
                )
                # pytype: enable=module-attr
            except AttributeError:
                # pytype: disable=attribute-error
                dest = repo.ui.expandpath(b'default')
                # pytype: enable=attribute-error

            peer = hg.peer(repo, {}, dest)
        except error.RepoError:
            peer = None

        flogheads = set()
        repoheads = set(repo.heads())

        # Get filelog heads from server and filter local heads with peer heads
        if peer is not None and peer.capable(b'getflogheads'):
            repoheads -= set(peer.heads())
            flogheads.update(set(peer.getflogheads(path)))

        # Get filelog heads from local repo heads.
        # This allows to get changes not yet pushed to the server. This is
        # also a fallback in case the server is not available.
        for head in repoheads:
            try:
                fctx = repo[head].filectx(path)
                fnode = node.hex(fctx.filenode())
                if fnode not in flogheads:
                    flogheads.add(fnode)
                    # We filter out parents of added node.
                    # A parent could already be in flogheads if still a
                    # head on the server, but not anymore in local repository
                    flogheads -= {node.hex(pfctx.filenode())
                                      for pfctx in fctx.parents()}
            except error.ManifestLookupError:
                pass

        return flogheads


class RevColorPalette:
    """Assign node and line colors for each revision"""

    def __init__(self):
        self._pendingheads = []
        self._knowncolors = {}
        self._curcolor = -1

    def _fillpendingheads(self, stoprev):
        if stoprev is None:
            return  # avoid filling everything (int_rev < None is False)

        nextpendingheads = []
        for p_ctxs, color in self._pendingheads:
            pending = self._fillancestors(p_ctxs, color, stoprev)
            if pending:
                nextpendingheads.append((pending, color))
        self._pendingheads = nextpendingheads

    def _fillancestors(self, p_ctxs, curcolor, stoprev):
        while p_ctxs:
            ctx0 = p_ctxs[0]
            rev0 = ctx0.rev()
            if rev0 < stoprev:
                return p_ctxs
            if rev0 in self._knowncolors:
                return
            self._knowncolors[rev0] = curcolor
            p_ctxs = ctx0.parents()

    def nextcolor(self):
        self._curcolor += 1
        return self._curcolor

    def __getitem__(self, ctx):
        rev = ctx.rev()
        if rev not in self._knowncolors:
            self._fillpendingheads(rev)
            if rev not in self._knowncolors:
                color = self.nextcolor()
                self._knowncolors[rev] = color
                p_ctxs = ctx.parents()
                self._pendingheads.append((p_ctxs, color))
        return self._knowncolors[rev]


@attr.s(slots=True, repr=False)
class GraphEdge:
    startrev = attr.ib()  # int or None (for working rev)
    endrev = attr.ib()  # int
    color = attr.ib()  # int
    linktype = attr.ib()  # one of LINE_TYPE

    def __repr__(self):
        return '%s(%r->%r, color=%r, linetype=%r)' % (
            self.__class__.__name__,
            self.startrev,
            self.endrev,
            self.color,
            self.linktype)

    @property
    def importance(self):
        """Sort key of overlapped edges; highest one should be drawn last"""
        # prefer parent-child relation and younger (i.e. longer) edge
        return -self.linktype, -self.color

class GraphNode:
    """Graph node for all actual changesets, as well as the working copy

    Simple class to encapsulate a hg node in the revision graph. Does
    nothing but declaring attributes.
    """
    __slots__ = ["bottomlines",
                 "extra",
                 "hidden",
                 "obsolete",
                 "rev",
                 "shape",
                 "toplines",
                 "instabilities",
                 "wdparent",
                 "x"]

    @classmethod
    def fromchangectx(cls, repo, ctx, xposition, lines):
        if ctx.thgmqappliedpatch():
            shape = NODE_SHAPE_APPLIEDPATCH
        elif ctx.closesbranch():
            shape = NODE_SHAPE_CLOSEDBRANCH
        elif phases.draft == ctx.phase():
            shape = NODE_SHAPE_REVISION_DRAFT
        elif phases.secret <= ctx.phase():
            shape = NODE_SHAPE_REVISION_SECRET
        else:
            shape = NODE_SHAPE_REVISION
        wdparent = ctx.node() in repo.dirstate.parents()
        return cls(shape, ctx=ctx, xposition=xposition, lines=lines,
                   wdparent=wdparent)

    @classmethod
    def fromfilectx(cls, repo, fctx, xposition, lines):
        ctx = repo.unfiltered()[fctx.rev()]  # get changectx wrapped by thgrepo
        obj = cls.fromchangectx(repo, ctx, xposition, lines)
        obj.extra = [fctx.path()]
        return obj

    def __init__(self, shape, ctx, xposition, lines,
                 wdparent=False, extra=None):
        self.rev = ctx.rev()
        self.hidden = ctx.hidden()
        self.obsolete = ctx.obsolete()
        self.instabilities = ctx.instabilities()
        self.shape = shape
        self.x = xposition
        self.bottomlines = lines
        self.toplines = []  # set from Graph.__getitem__
        self.wdparent = wdparent
        self.extra = extra

    @property
    def faded(self):
        """Indicates whether the node should be faded in the UI"""
        return self.hidden or self.obsolete

    @property
    def cols(self):
        """Number of columns for the node"""
        return max([self.x] + [max(p) for p, _e in self.bottomlines]) + 1


class PatchGraphNode:
    """Node for un-applied patch queue items.

    This node is always displayed unfaded and only occupy one column.
    Furthermore, the revision is the name of the patch.
    """

    def __init__(self, name):
        self.name = name
        self.shape = NODE_SHAPE_UNAPPLIEDPATCH
        self.rev = name  # unapplied patch uses its name as rev
        self.wdparent = False
        self.toplines = []
        self.bottomlines = []
        self.instabilities = ()
        self.x = 0

    @property
    def faded(self):
        """Indicates whether the node should be faded in the UI"""
        return False

    @property
    def cols(self):
        """Number of columns for the node"""
        return 1


class Graph:
    """
    Graph object to ease hg repo navigation. The Graph object
    instantiate a `revision_grapher` generator, and provide a `fill`
    method to build the graph progressively.
    """

    def __init__(self, repo, grapher):
        self.repo = repo
        self.grapher = grapher
        self.nodes = []
        self.nodesdict = {}

    def __getitem__(self, idx):
        if isinstance(idx, slice):
            # XXX TODO: ensure nodes are built
            return self.nodes.__getitem__(idx)
        if idx >= len(self.nodes):
            # build as many graph nodes as required to answer the
            # requested idx
            self.build_nodes(idx)
        if idx >= len(self):
            return self.nodes[-1]
        return self.nodes[idx]

    def getrevstate(self, idx):
        """Return (rev, isunapplied) of the node at the specified row"""
        n = self[idx]
        return n.rev, False

    def __len__(self):
        # len(graph) is the number of actually built graph nodes
        return len(self.nodes)

    def build_nodes(self, nnodes=None, rev=None):
        """
        Build up to `nnodes` more nodes in our graph, or build as many
        nodes required to reach `rev`.

        If both rev and nnodes are set, build as many nodes as
        required to reach rev plus nnodes more.
        """
        if self.grapher is None:
            return False

        usetimer = nnodes is None and rev is None
        if usetimer:
            if os.name == "nt":
                timer = time.clock  # pytype: disable=module-attr
            else:
                timer = time.time
            startsec = timer()
            nnodes = -1  # infinite
        elif nnodes is None:
            nnodes = 0

        if rev is not None and self.nodes:
            gnode = self.nodes[-1]
            if isinstance(gnode.rev, int) and gnode.rev <= rev:
                rev = None  # already reached rev
        if rev is None and nnodes == 0:
            return True
        for gnode in self.grapher:
            if self.nodes:
                gnode.toplines = self.nodes[-1].bottomlines
            self.nodes.append(gnode)
            self.nodesdict[gnode.rev] = gnode
            if rev is None:
                nnodes -= 1
            elif isinstance(gnode.rev, int) and gnode.rev <= rev:
                rev = None  # we reached rev, switching to nnode counter
            if rev is None and nnodes == 0:
                return True
            if usetimer:
                cursec = timer()
                if cursec < startsec or cursec > startsec + 0.1:
                    return True

        self.grapher = None
        return False

    def isfilled(self):
        return self.grapher is None

    def _bottom_rev(self):
        """Returns the known lowest revision number

        If nodes == {} or {wctx}, the lowest revision is not yet known, so
        returns None.
        """
        if not self.nodes:
            return None
        return self.nodes[-1].rev

    def index(self, rev):
        brev = self._bottom_rev()
        if brev is None:
            # graph is empty, let's build some nodes.  nodes for unapplied
            # patches are built at once because they don't have comparable
            # revision numbers, which makes build_nodes() go wrong.
            self.build_nodes(10, len(self.repo) - 1)
            brev = self._bottom_rev()
        if isinstance(rev, int) and brev is not None and rev < brev:
            self.build_nodes(brev - rev)
        try:
            return self.nodes.index(self.nodesdict[rev])
        except KeyError:
            raise ValueError('rev %r not found' % rev)

    #
    # File graph method
    #

    def filename(self, rev):
        return self.nodesdict[rev].extra[0]


class GraphWithMq:
    """Graph layouter that also shows un-applied mq changes"""

    def __init__(self, graph, patchnames):
        object.__init__(self)
        self.graph = graph
        self._patchnames = list(reversed(patchnames))

    def isfilled(self):
        """Indicates whether the graph is done computing"""
        return self.graph.isfilled()

    def build_nodes(self, fillstep=None, rev=None):
        """Ensures that the graph layout is computed"""
        self.graph.build_nodes(fillstep, rev)

    def __len__(self):
        return len(self._patchnames) + len(self.graph)

    def __getitem__(self, row):
        if row < len(self._patchnames):
            return PatchGraphNode(self._patchnames[row])
        return self.graph[row - len(self._patchnames)]

    def getrevstate(self, row):
        """Return (rev, isunapplied) of the node at the specified row"""
        if row < len(self._patchnames):
            return self._patchnames[row], True
        return self.graph.getrevstate(row - len(self._patchnames))

    def index(self, rev):
        """Get row number for specified revision"""
        if isinstance(rev, bytes):
            return self._patchnames.index(rev)
        i = self.graph.index(rev)
        return len(self._patchnames) + i
