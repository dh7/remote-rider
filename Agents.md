# Remote Rider Agent Notes

- Control hub runs on the user's Mac (`start-control.sh`).
- Stop it with `./start-control.sh --stop` or `./stop-control.sh`.
- Remote stack runs on netochka (`start-remote.sh`).
- When shipping changes that affect both control UI/API and remote runtime, push once and pull on both machines.
- Typical rollout:
  1. Pull on Mac, restart control hub.
  2. Pull on netochka, restart remote stack.
