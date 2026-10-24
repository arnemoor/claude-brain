---
name: brain-contribute
description: Prepare changed shared brain pages as a batched pull request. Shows the changed pages, requires explicit confirmation, pushes a contribution branch, and opens a pull request without ever pushing the shared default branch. Use for /brain-contribute and requests to share brain knowledge with the team.
model: sonnet
---

> **First-run check.** Before doing anything else, read `~/.config/brain/config.json`. If it is missing or incomplete, invoke the **brain-init** skill to set up the brain, then continue this task. If present, use its roots and `freshnessSources`.

# brain-contribute

Share a batch of knowledge from the shared repository at `sharedTree.path`. A vault without `sharedTree` (a solo vault) has nothing to contribute: say so and stop. Never push its default branch directly. Catalogs are gitignored and must never be included.

## 1. Find work to contribute
Read `sharedTree.path` from `~/.config/brain/config.json`. Detect both uncommitted changes and commits not present on the remote tracking branch. If there are neither, stop politely and say there is nothing to contribute.

Identify the shared repository default branch from `origin/HEAD`. Show the changed page paths, excluding `index.md`, every `MUSTKNOW.md`, `log.md`, and any generated catalog. Include changes from both the working tree and unpushed commits. Do not proceed until the user gives explicit confirmation of this batch.

## 2. Use the member contribution branch
After confirmation, get the member userid with `whoami`. Use that userid itself as the shared contribution branch name.

This branch is long-lived and is reused for every contribution. If it does not exist, create it. If it exists, check it out and continue on it. Keep any existing unpushed work on this branch. Stage only the confirmed shared pages and commit the batch with a concise message. Never add catalogs because they are gitignored. Then rebase the branch on the detected default branch so its pull request stays clean. This order matters because rebase requires a clean working tree and refuses the confirmed pages while they are still uncommitted. If the rebase reports conflicts, stop, report the conflicting files, and do not push a half-rebased branch.

## 3. Push and update or open the pull request
Push the userid branch to origin. Pick the pull request tool from the origin host: `gh` for GitHub, `tea` for Gitea. Before creating a pull request, check whether one is already open from that branch to the detected shared default branch (`gh pr list --head <branch>` or `tea pulls list`). If one is open, say that the push updates the existing pull request and do not create another. Otherwise open one from that branch against the detected shared default branch (`gh pr create --base <default> --head <branch>` or `tea pulls create --base <default> --head <branch>`). For any other host, print the branch and the host's pull request URL and let the user open it. The pull request description must list every changed page. Never push the default branch directly.

Report the branch and pull request. Remind the user that teammates see nothing until the pull request merges.
