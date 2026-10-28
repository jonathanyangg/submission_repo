# Coachbot submissions

This repository is the queue for the [Coachbot swarm](https://coachbotswarm.github.io/User-Guide/). Today, a user pushes a `<github-username>.json` file here. A script on the lab computer polls `main`, deletes the file, and runs the experiment named by that file.

This branch proposes a second path: a pull request that contains the experiment itself, checked by GitHub Actions before anyone walks over to the robots. The JSON path is unchanged. Nothing in this branch turns the lab poller off.

Read [docs/MIGRATION.md](docs/MIGRATION.md) for the user steps, the one-time lab setup, the security rules, and the questions this proposal needs answered.

## What a submission is

```text
submissions/<name>/usr_code.py
submissions/<name>/init_pose.csv
```

`usr_code.py` defines `usr(robot)`. `init_pose.csv` is `id,x,y,theta` under the rules in the user guide: ids 0–49, at most 50 robots, at least 0.25 m apart, x in [-1.2, 1.0] m, y in [-1.4, 2.35] m, theta in (-2π, 2π).

[submissions/example-orbit](submissions/example-orbit) is a two-robot orbit that satisfies those rules. Robot 0 stays put and broadcasts its pose. Robot 1 orbits that pose at about 0.5 m.

## What runs where

| Workflow | Runner | When |
| --- | --- | --- |
| [Validate submission](.github/workflows/validate.yml) | GitHub-hosted | every pull request, or a manual run |
| [Comment validation result](.github/workflows/comment.yml) | GitHub-hosted | after validation, so fork pull requests still get a comment |
| [Hardware run](.github/workflows/hardware.yml) | lab computer, one job at a time | a maintainer adds the `hardware-run` label, or dispatches the workflow from `main` |

Validation checks the files, the pose rules, Python 2.7 syntax, and Python 3 syntax, then runs about 60 simulated seconds of [swarm-simulator](https://github.com/michelleezhang/swarm-simulator) with the GUI off. The hardware job does not start until a reviewer approves the `swarm` environment. It calls the lab's existing run script through `COACHBOT_RUN_SCRIPT`. That script is not in this repository. Results are Actions artifacts linked from the pull request, not new commits.

## Check it locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-simulator.txt
PY2_BIN=/path/to/python2.7 .venv/bin/python -m unittest discover -s tests -v
```

`PY2_BIN` can be omitted when Docker is available; the syntax check then uses `python:2.7.18-slim`. The robots run 2.7.16. 2.7.18 is the last 2.7 release and accepts the same syntax.
