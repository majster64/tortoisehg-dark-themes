# obsolete related util functions (taken from hgview)
#
# The functions in this file have been taken from hgview's util.py file
# (http://hg.logilab.org/review/hgview/file/default/hgviewlib/util.py)
#
# Copyright (C) 2009-2012 Logilab. All rights reserved.
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

from mercurial import error

def predecessorsmarkers(obsstore, node):
    return obsstore.predecessors.get(node, ())

def successorsmarkers(obsstore, node):
    return obsstore.successors.get(node, ())

def first_known_predecessors_rev(repo, rev):
    if rev is None or not isinstance(rev, int):
        return

    obsstore = getattr(repo, 'obsstore', None)
    if not obsstore:
        return

    clog = repo.changelog
    get_rev = clog.index.get_rev

    start = clog.node(rev)
    markers = predecessorsmarkers(obsstore, start)
    candidates = {mark[0] for mark in markers}
    seen = set(candidates)
    if start in candidates:
        candidates.remove(start)
    else:
        seen.add(start)
    while candidates:
        current = candidates.pop()
        crev = get_rev(current)
        if crev is not None:
            try:
                repo[crev]  # filter out filtered revisions
                yield crev
                continue
            except error.RepoLookupError:
                pass
        for mark in predecessorsmarkers(obsstore, current):
            if mark[0] not in seen:
                candidates.add(mark[0])
                seen.add(mark[0])

def first_known_predecessors(ctx):
    for rev in first_known_predecessors_rev(ctx.repo(), ctx.rev()):
        yield ctx.repo()[rev]

def first_known_successors(ctx):
    obsstore = getattr(ctx.repo(), 'obsstore', None)
    startnode = ctx.node()
    get_rev = ctx.repo().changelog.index.get_rev

    if obsstore is not None:
        markers = successorsmarkers(obsstore, startnode)
        # consider all predecessors
        candidates = set()
        for mark in markers:
            candidates.update(mark[1])
        seen = set(candidates)
        if startnode in candidates:
            candidates.remove(startnode)
        else:
            seen.add(startnode)
        while candidates:
            current = candidates.pop()
            # is this changeset in the displayed set ?
            crev = get_rev(current)
            if crev is not None:
                try:
                    yield ctx.repo()[crev]
                    continue
                except error.RepoLookupError:
                    # filtered-out changeset
                    pass
            for mark in successorsmarkers(obsstore, current):
                for succ in mark[1]:
                    if succ not in seen:
                        candidates.add(succ)
                        seen.add(succ)
