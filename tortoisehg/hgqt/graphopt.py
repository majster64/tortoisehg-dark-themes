# Copyright (c) 2016 Unity Technologies.
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation; either version 2 of the License, or (at your option) any later
# version.

"""helper functions for efficiently computing graphs for the revision history

This module provides an optimised model for computing graph layouts that are
identical to, but with different colored edges to the normal graph layout
algorithm. The optimised model is both faster to compute and uses less
memory for repositories with a lot of history.
"""

from __future__ import annotations

import collections
import itertools
import typing

from mercurial import (
    pycompat,
    revset,
    util,
)

from ..util import (
    obsoleteutil,
)
from . import (
    graph as graphmod,
)
from .graph import (
    LINE_TYPE_PARENT,
    LINE_TYPE_FAMILY,
    LINE_TYPE_OBSOLETE,
)

if typing.TYPE_CHECKING:
    from typing import (
        List,
        Tuple,
    )

def _compute_lines(rev: int,
                   prevs: List[int],
                   revs: List[int],
                   active_edges: List[GraphEdge]) -> List[Tuple[Tuple[int, int], GraphEdge]]:
    """Computes current index and next line's index of each active edge

    Args:
        active_edges (list[GraphEdge]): Edges relevant for the current line.
        rev (int): The current revision.
        prevs (list[int]): Index of nodes for the previous line.
        revs (list[int]): Index of nodes for the current line.
    """

    lines = []
    for edge in active_edges:
        if edge.startrev == rev:
            start_rev = edge.startrev
        else:
            start_rev = edge.endrev
        pos = (prevs.index(start_rev), revs.index(edge.endrev))
        lines.append((pos, edge))
    return lines

def _create_node(repo, rev, index_info):
    ctx = repo[rev]
    xposition, prevs, revs, actedges = index_info
    lines = _compute_lines(rev, prevs, revs, actedges)
    return graphmod.GraphNode.fromchangectx(repo, ctx, xposition, lines)


class GraphEdge:
    """Edge from startrev to endrev"""

    def __init__(self, graph, startrev, endrev, linktype):
        self.graph = graph
        self.startrev = startrev
        self.endrev = endrev
        self.linktype = linktype
        self._color = None

    @property
    def color(self):
        """Color of the edge, uses branch color of endrev"""

        if self._color is None:
            self._color = self.graph.edge_color(
                self.graph.repo[self.endrev].branch())
        return self._color

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
        return -self.linktype, -self.endrev


def _branch_spec(repo, branch, all_parents):
    if all_parents:
        return repo.revs(b'::branch(%s)', branch)
    else:
        return repo.revs(b'branch(%s)', branch)


class Graph:
    """Efficient graph layouter for repositories.

    The grapher pre-computes the overall layout of the graph in a memory
    and time efficient way, caching enough data that the current view can
    be calculated efficiently without putting an unreasonable amount of
    strain on system memory usage for large repositories.

    Currently, it is possible to visualise a few hundred nodes and keep the
    layout of a 200k+ changeset repository in about 2.4 GB memory, compared
    to about 14 GB memory for the existing graph layout algorithm.

    To achieve efficiency, this layouter does not support drawing
    graft or obsoletion edges."""

    def __init__(self, repo, opts):
        self._repo = repo
        self._graph = {}
        self._cache = util.lrucachedict(1000)
        self._revset_set = opts.get('revset', set())
        self._revset_set_pure = self._revset_set
        self._revset = sorted(self._revset_set, reverse=True)
        self._all_parents = opts.get('allparents', False)
        self._show_family_line = opts.get('showfamilyline', False)
        self._show_graft_source = opts.get('showgraftsource', False)
        branch = opts.get('branch', '')
        if branch:
            revs = _branch_spec(self._repo, branch, self._all_parents)

            add_none = False
            if self._revset:
                self._revset_set = self._revset_set & frozenset(revs)
            else:
                self._revset_set = frozenset(revs)
                prevs = {pctx.rev() for pctx in self._repo[None].parents()}
                if prevs & self._revset_set:
                    add_none = True

            self._revset = sorted(self._revset_set, reverse=True)

            if add_none:
                self._revset.insert(0, None)
                self._revset_set_pure = self._revset_set
                self._revset_set = frozenset(self._revset_set | {None})
        self._edge_color_cache = {}
        self._grapher = self._build_nodes()
        self._row_to_rev = dict(
            enumerate(self._get_revision_iterator()))

    @property
    def _clean_revset_set(self):
        if self._revset_set_pure:
            return self._revset_set_pure
        return self._revset_set

    def edge_color(self, branch):
        """Color function for edges"""
        idx = self._edge_color_cache.get(branch, None)
        if idx is None:
            idx = graphmod.hashcolor(branch)
            self._edge_color_cache[branch] = idx
        return idx

    def isfilled(self):
        """Indicates whether the graph is done computing.

        We prefer to cheat the repository model into recreating rather than
        letting it try to overwrite parts of the model as it does not fit
        with the normal layouting model. This value is thus always false."""
        return False

    @property
    def repo(self):
        """Repository instance for the graph"""
        return self._repo

    def _workingdir_parents(self):
        parents = [ctx.rev() for ctx in self._repo[None].parents()]
        if len(parents) == 1:
            parents.append(-1)
        return parents

    def _get_revision_iterator(self):
        if self._revset:
            revs = revset.spanset(self._repo, max(self._clean_revset_set),
                                  min(self._clean_revset_set) - 1)
            if None not in self._revset_set:
                return revs
            return itertools.chain([None], revs)
        else:
            revs = revset.spanset(self._repo, len(self._repo) - 1, -1)
            return itertools.chain([None], revs)

    def _filter_parents(self, p1, p2):
        """omit parents not in revset as well as degenerate repository cases"""

        if self._revset:
            if p1 not in self._revset_set:
                p1 = -1
            if p2 not in self._revset_set:
                p2 = -1
        if p1 == p2:
            p2 = -1
        return p1, p2

    def _find_family(self, family, rev, op, p, op2, p2):
        if not self._revset:
            return [p, p2]

        include_p1 = op != -1 and p == -1
        include_p2 = op2 != -1 and p2 == -1
        if not include_p1 and not include_p2:
            return [p, p2]

        rv = []
        if not include_p1:
            rv.append(p)
        if not include_p2:
            rv.append(p2)

        fam = family[rev]
        candidates = [c for c, cisp1 in fam if
                      include_p1 and cisp1 or include_p2 and not cisp1]

        if not candidates:
            return rv

        if len(candidates) == 1:
            return rv + candidates

        remove = set()
        for candidate in candidates:
            remove |= {c for c, _ in family[candidate]}

        return sorted(rv + list(set(candidates) - remove), reverse=True)

    def _pre_compute_family(self, parentrevs):
        """
        Calculates the ancestry of all involved changesets if we're displaying
        a revset and showing family lines are enabled.

        A family line is added to an edge if there is no direct parent connected
        to it and there is an ancestral relationship between the two. If the
        ancestor can be reached both through the first and second parent, only
        the first parent ancestor edge is rendered.

        Args:
            parentrevs (dict[int,list[int]]): Parents for each revision.
        """

        if not self._revset or not self._show_family_line:
            return None

        anc = collections.defaultdict(set)
        holders = collections.defaultdict(set)

        revrange = self._get_revision_iterator()

        for r in revrange:
            p1, p2 = parentrevs[r]

            toupdate = holders.pop(r, ())
            if not toupdate and r not in self._revset_set:
                continue

            for origrev, currentrev, isp1, ndup in toupdate:
                if not ndup:
                    if p1 != -1 and p1 in self._revset_set:
                        anc[origrev].add((p1, isp1))
                    if p2 != -1 and p2 in self._revset_set:
                        anc[origrev].add((p2, isp1))

                if p1 != -1:
                    if p1 not in self._revset_set:
                        holders[p1].add((origrev, p1, isp1, ndup))
                    else:
                        anc[origrev].add((p1, isp1))
                if p2 != -1:
                    if p2 not in self._revset_set:
                        holders[p2].add((origrev, p2, isp1, ndup))
                    else:
                        anc[origrev].add((p2, isp1))

            if r in self._revset_set:
                if p1 != -1:
                    holders[p1].add((r, p1, True, p1 not in self._revset_set))
                    if p1 in self._revset_set:
                        anc[r].add((p1, True))
                if p2 != -1:
                    holders[p2].add((r, p2, False, p2 not in self._revset_set))
                    if p2 in self._revset_set:
                        anc[r].add((p2, False))
                continue

        return anc

    def _add_obsolete(self, rev, parents_to_add, actedge):
        """Resolves obsolete edges.

        This is a mangled copy from obsoleteutil.first_known_predecessors that
        avoids using context lookups, except to determine filtering state.
        """

        if self._revset and rev not in self._revset_set:
            return

        revs = list(obsoleteutil.first_known_predecessors_rev(self._repo, rev))

        if self._revset:
            revs = [r for r in revs if r in self._revset_set]
        for r in revs:
            actedge[r].append(GraphEdge(self, rev, r, LINE_TYPE_OBSOLETE))
            parents_to_add.append(r)

    def _build_nodes(self):
        """
        Generator for computing necessary information to layout the graph.

        For each revision the previous node indexes are computed, the current
        node indexes, as well as the active edges that should be rendered.

        Returns: Revision just processed.
        """

        clog = self._repo.changelog
        parentrevs = {r: clog.parentrevs(r) for r in clog}
        parentrevs[None] = self._workingdir_parents()
        actedge = collections.defaultdict(list)
        revs = []
        revrange = self._get_revision_iterator()
        family = self._pre_compute_family(parentrevs)

        for rev in revrange:
            addparents = not self._revset_set or rev in self._revset_set
            if rev not in revs and addparents:
                revs.append(rev)
            rev_index = revs.index(rev) if addparents else 0
            if rev in actedge:
                del actedge[rev]
            op1, op2 = parentrevs[rev]
            p1, p2 = self._filter_parents(op1, op2)
            p1l, p2l = LINE_TYPE_PARENT, LINE_TYPE_PARENT

            # compute family lines if enabled and we're in revset mode
            if self._revset and addparents and self._show_family_line:
                fp1, fp2 = p1, p2
                parents = self._find_family(family, rev, op1, p1, op2, p2)
                if addparents:
                    for p in parents:
                        if p != -1:
                            pl = LINE_TYPE_FAMILY if p != fp1 and p != fp2 else p1l
                            actedge[p].append(GraphEdge(self, rev, p, pl))

                parents_to_add = [p for p in parents if
                                  p != -1 and p not in revs]
            else:
                if p1 != -1 and addparents:
                    actedge[p1].append(GraphEdge(self, rev, p1, p1l))
                if p2 != -1 and addparents:
                    actedge[p2].append(GraphEdge(self, rev, p2, p2l))

                parents_to_add = [p for p in (p1, p2) if
                                  p != -1 and p not in revs]

            if self._show_graft_source:
                self._add_obsolete(rev, parents_to_add, actedge)

            prevs = revs[:]
            if addparents:
                revs[rev_index:rev_index + 1] = parents_to_add
            self._graph[rev] = (
                rev_index, prevs, revs[:],
                list(itertools.chain(*list(actedge.values()))))

            yield rev

    def build_nodes(self, fillstep=None, rev=None):
        """Ensures that the graph layout is computed to the specified point.

        If fillstep is specified, it is the number of revisions to fetch from
        the current point.

        If rev is specified, it is the revision that we must load until.
        """

        if self._grapher is None:
            return

        if rev is not None:
            if rev not in self._graph:
                while True:
                    r = next(self._grapher, -1)
                    if r == -1:
                        self._grapher = None
                        break
                    if r is not None and r < rev:
                        break

        if fillstep is not None:
            if self._grapher:
                for i in pycompat.xrange(0, fillstep):
                    if next(self._grapher, -1) == -1:
                        self._grapher = None
                        break

    def __len__(self):
        """Returns the number of revisions in the graph.

        If we are not in revset mode, this also includes an extra revision for
        the working context.
        """

        if self._revset:
            return len(self._revset)
        return len(self._row_to_rev)

    def _get_or_load_graph_node(self, rev):
        """Retrieve node from graph cache.

        This will load graph layout information until rev is reached if it is
        not already available, potentially blocking for a while.
        """

        if rev not in self._graph:
            self.build_nodes(rev=rev)

        return self._graph[rev]

    def _rev_from_row(self, row):
        if self._revset:
            if row < 0 or row >= len(self._revset):
                return self._revset[-1]

            return self._revset[row]

        return self._row_to_rev[row]

    def __getitem__(self, row):
        """Entry point for repomodel to fetch individual rows for display.

        Once the overall layout is determined, a node is either fetched from
        the internal LRU cache, or instantiated, which will trigger the final
        computation of edge layout of the row.
        """

        rev = self._rev_from_row(row)
        if rev in self._cache:
            node = self._cache[rev]
            if node is not None:
                return node

        idxinfo = self._get_or_load_graph_node(rev)
        node = _create_node(self._repo, rev, idxinfo)
        if row > 0:
            prevrev = self._rev_from_row(row - 1)
            if (not self._revset or None in self._revset) or prevrev is not None:
                pidxinfo = self._get_or_load_graph_node(prevrev)
                _pprevs, _prevs, _pactedges = pidxinfo[1:]
                plines = _compute_lines(prevrev, _pprevs, _prevs, _pactedges)
                node.toplines = plines[:]
        self._cache[rev] = node

        return node

    def getrevstate(self, row):
        """Return (rev, isunapplied) of the node at the specified row"""
        return self._rev_from_row(row), False

    def index(self, rev):
        """Get row number for specified revision"""

        idx = 0
        isrevset = bool(self._revset)
        for iter_rev in self._get_revision_iterator():
            if iter_rev == rev:
                return idx
            if not isrevset or iter_rev in self._revset_set:
                idx += 1

        raise ValueError('rev %r not found' % rev)
