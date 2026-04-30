# Remote Rider Agent Notes

- Never clone GitHub repos over HTTPS on homelab machines.
- Never leave `origin` as `https://github.com/...` on homelab machines.
- Always use SSH remotes in the form `git@github.com:owner/repo.git`.
- Control hub runs on the user's Mac (`start-control.sh`).
- Stop it with `./start-control.sh --stop` or `./stop-control.sh`.
- Remote stack runs on netochka (`start-remote.sh`).
- When shipping changes that affect both control UI/API and remote runtime, push once and pull on both machines.
- Typical rollout:
  1. Pull on Mac, restart control hub.
  2. Pull on netochka, restart remote stack.
