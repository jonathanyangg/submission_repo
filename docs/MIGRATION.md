# From the JSON queue to pull requests

This is a proposal for how experiments get onto the Coachbot swarm. It is written against the [user guide](https://coachbotswarm.github.io/User-Guide/) as updated on 12 November 2025. An earlier lab note described the 2024 layout (`Code_Queue/`, results committed under `Completed_Runs/`). That is not how the guide describes the system now, and this document follows the guide.

Nothing here deletes the current path. The lab computer can keep polling `main` for `<username>.json` until someone turns that poller off.

## Why a folder, not another JSON pointer

A submission today is a JSON file in this repository:

```json
{
  "usercode": "usr_code.py",
  "initialpositions": "init_pose.csv",
  "email": "email.txt"
}
```

The file names the three inputs. The inputs themselves live on `main` of a private repository named after the user's GitHub account. The lab script clones that private repository, deletes the JSON from this one, and later pushes a timestamped branch of results back.

That pointer cannot be checked on a GitHub-hosted runner without a credential that can read every private user repository. The diff of the pull request would be three filenames, not the code a reviewer is about to put on the robots.

A directory in the pull request is the experiment:

```text
submissions/<name>/usr_code.py
submissions/<name>/init_pose.csv
```

The reviewer sees the code and the start poses. The validation job reads those files and nothing else. No per-user repository, no `email.txt`, and no token that can clone private repos. GitHub already emails the pull request author.

`workflow_dispatch` is the manual version of the same idea. From the Actions tab, on the `main` branch, a maintainer can re-check a folder or send one to the lab computer by typing `submissions/<name>` and a commit SHA. That is the escape hatch when a run should be repeated without a new pull request.

`email.txt` may still sit in a folder. The checks ignore it and do not copy the address into the pull-request comment.

## What a user does

1. Fork this repository.
2. Add `submissions/<name>/usr_code.py` and `init_pose.csv`. `<name>` is a short label, not necessarily a GitHub username. The [orbit example](../submissions/example-orbit) is a working starting point.
3. Open a pull request into `main`.
4. Read the validation comment. It reports the pose rules, whether the file parses as Python 2.7 and as Python 3, and whether a 60 second headless simulation finished.
5. Wait. The robots do not start because a pull request exists. A maintainer adds the `hardware-run` label and approves the `swarm` environment.
6. Download the `hardware-results` artifact from the Actions run linked in the comment. The artifact is the overhead video, `ID_mapping.csv`, pose logs, and `automation_errors`, if the lab script wrote them. They are not committed.

Users do not need write access to this repository, and they should not be given it. Forks and pull requests are the whole contribution path.

`usr_code.py` has to define a top-level `usr(robot)`. The same file is uploaded to every robot listed in the pose file. The robots run Python 2.7.16. The simulator imports the file under Python 3. Code has to parse as both: no Python 2 `print` statements, no f-strings, no `async`. `robot.virtual_id()` is a method on the hardware and a number in the simulator. The orbit example handles both. Wheel speeds in the example stay inside ±50, and `set_vel` is called on every loop because a hardware command lasts one second.

Messages are limited to 64 bytes on the robots. The usual pattern is `struct.pack`, which the orbit example uses for two floats (8 bytes).

## What validation checks

The [validate workflow](../.github/workflows/validate.yml) runs on a GitHub-hosted runner, not on the lab computer.

| Check | Rule |
| --- | --- |
| Files | `usr_code.py` and `init_pose.csv` are ordinary files, UTF-8, under 512 KiB, not symlinks |
| Poses | ids are whole numbers 0–49 and unique; at most 50 rows; x in [-1.2, 1.0]; y in [-1.4, 2.35]; theta in (-2π, 2π); every pair at least 0.25 m apart |
| Python 2.7 | `python:2.7.18-slim` runs `ast.parse` and requires `def usr(robot)`. The file is not imported. 2.7.18 is the last 2.7 release; the syntax matches 2.7.16 |
| Python 3 | the same parser under Python 3, because the simulator will import the file |
| Simulation | about 60 simulated seconds of `swarm-simulator` at real-time factor 100, GUI off (`USE_VIS` 0), dummy SDL driver, message size 64 bytes |

The simulator is pinned to [michelleezhang/swarm-simulator](https://github.com/michelleezhang/swarm-simulator) commit `25c8ea31589cdc4c7371be7b371c4774853f0483` (25 September 2024). The user guide links `michelleezhang/swarm_simulation`, which is an older tree. The pin should be confirmed; see the questions below.

The smoke test does not model the charging rails or the exact arena. It answers "does this file run for a minute without throwing." A program that returns immediately, or that drives out of the real field, can still pass. Pose rejection and tracebacks are the checks that replace the "your poses were invalid" email.

Logs are the `validation-logs` artifact: simulator stdout, stderr, and `swarm_state_log.csv`. The pull-request comment is a short pass or fail. On a same-repository pull request the validate job comments directly. On a fork, that token is read-only, so [comment.yml](../.github/workflows/comment.yml) comments after the run, using the workflow file on `main`. That second workflow does nothing until this proposal is merged, because GitHub only runs `workflow_run` from the default branch.

A pull request that does not touch `submissions/` passes without running the simulator. At most eight submission folders are checked per run.

## What a maintainer does for one hardware run

1. Read the validation comment and the diff.
2. On the pull request, add the label `hardware-run`. The label does not exist until someone creates it. Any color is fine.
3. GitHub opens an approval request for the `swarm` environment. Approve it only after looking at the diff. Turn on "prevent self-review" if more than one person can approve.
4. The lab computer picks up the job when it is free. The job calls `COACHBOT_RUN_SCRIPT` and uploads `hardware-results`.
5. The pull request gets a second comment with the Actions URL. The user downloads the artifact. Merging the pull request is optional; the run does not depend on it.

To repeat a run without a new pull request, open Actions, choose "Hardware run", select the `main` branch, and fill in:

- `submission_dir`: `submissions/<name>`
- `git_ref`: the 40-character commit to read
- `repository`: `owner/name` when that commit is on a fork
- `pr_number`: optional, so the comment has a place to land

Dispatch from `main`. A dispatch from any other branch is ignored by this workflow file, and a branch that deleted that guard would be running its own workflow. That only matters if untrusted people can push branches. They should not be able to.

## One-time setup

Do these in order. Registering the runner before the environment has required reviewers would let a job start with no approval, because GitHub creates a missing environment without protection rules.

1. Decide public or private. The tradeoff is the next section. This proposal is written for a public repository plus forks, which works on the free plan.
2. Protect `main`: pull requests required, no direct pushes. Remove the write access that queue users have today once the poller is off. Until then, a user who can push to `main` can also edit workflow files.
3. Create an environment named `swarm`. Add required reviewers. Enable "prevent self-review" if the reviewer list has more than one person.
4. Add an environment secret `COACHBOT_RUN_SCRIPT` whose value is the absolute path of the lab script, for example `/opt/coachbot/run_submission`. Put it on the `swarm` environment, not as a repository secret, so only the approved job receives it.
5. On the lab computer, install a self-hosted runner for this repository. Give it the label `coachbot-lab`. The built-in `self-hosted` label is added automatically. The workflow asks for both. Register one runner. A self-hosted runner runs one job at a time; a second runner with the same label would run a second experiment beside the first.
6. Leave the runner's outbound HTTPS access to GitHub in place. It does not need an inbound port. Do not enable the repository setting "Run workflows from fork pull requests" for self-hosted runners. That setting is what would let an untrusted workflow file land on the lab computer.
7. Confirm `python3` on the lab computer is 3.9 or newer. The hardware job only runs the trusted glue in `coachbot_ci/`. It does not need pygame, and it does not import `usr_code.py`.
8. Confirm the private lab script accepts the arguments below, or add a small adapter next to it. The adapter is the only new code on the lab machine.

The runner registration command is the one GitHub shows under Settings → Actions → Runners. The labels flag should include `coachbot-lab`.

## The script this repository does not contain

`coachbot_ci/hardware.py` is a placeholder. If `COACHBOT_RUN_SCRIPT` is unset, the job writes `NOT_CONFIGURED.txt` and stops. It does not invent charging, positioning, or the robot runtime.

When the variable is set, the approved job executes exactly:

```text
$COACHBOT_RUN_SCRIPT \
    --submission-dir <directory> \
    --output-dir <directory> \
    --timeout-sec 600
```

`<directory>` for the submission contains `usr_code.py` and `init_pose.csv` as regular files. `--timeout-sec 600` is the user-code cap from the guide (10 minutes). Positioning and charging are inside the lab script and may take longer. The workflow kills the script after 40 minutes and the job itself after 45 minutes. Those backstops are not the user-code cap.

The script is expected to keep doing what it does today: validate if it wants to, charge, place the robots, run, and write the usual products into `--output-dir` (`camera_video.mp4` or the overhead video, `ID_mapping.csv`, pose CSVs, per-robot logs, `automation_errors`). Exit 0 when that finished. The files are uploaded as an artifact. They are not pushed to a branch.

The child process does not receive `GITHUB_TOKEN` or other token-shaped variables. The glue refuses a script path that points inside the submission directory, and it refuses symlinks for the two submission files. It never executes `usr_code.py` itself.

## How this maps onto the guide

| Today | This proposal |
| --- | --- |
| Email `coachbotswarmsystem@gmail.com` to join the org | Fork the repository and open a pull request. Joining the org is not required for a submission |
| Private repository named after the GitHub user, with skeleton files | The pull request holds the files. Old private repositories can stay as archives of past results |
| Push `<username>.json` to `submission_repo` `main` | Open a pull request that adds `submissions/<name>/` |
| Lab script polls, then deletes the JSON | GitHub Actions is the queue. The JSON file is not deleted by these workflows, so a poller that is still running will not see pull-request contents |
| Pose rules enforced on the lab computer, user emailed on failure | The same rules run on GitHub before a person looks. The comment replaces that email |
| Up to three emails (valid, started, finished) | Comments on the pull request, which GitHub already emails |
| Results on a timestamped branch of the private user repository | `hardware-results` artifact, linked from the pull request, retained 90 days unless the retention is changed |
| 10 minute cap | Passed through as `--timeout-sec 600` for the lab script to enforce |
| One lab computer, one run at a time | One self-hosted runner, plus a concurrency group `coachbot-swarm-hardware` |

The concurrency group is on the hardware job, not on label events that do not request a run. It keeps a second hardware job from starting while one is in progress. GitHub still keeps only one waiting job behind the running one and cancels an older waiting job. Approving many runs at once is not a reliable queue. Approve the next one when the current run has started or finished. One registered runner is the real mutex.

## Leaving the old path in place

These workflows do not watch for JSON files, and they do not delete them. While the poller is still running:

- A push of `<username>.json` to `main` is still a hardware submission, with no pull request and no GitHub-side pose check.
- A pull request does not by itself start the robots, and the poller will not see the files inside it.

Retire the old path only after a run has gone through the new one end to end:

1. Point `COACHBOT_RUN_SCRIPT` at the real script, or at an adapter, and complete one approved run.
2. Stop the poller so it cannot delete a JSON file out from under a person who has not switched yet.
3. Tell current users that new experiments are pull requests. Leave their private repositories readable so old timestamped branches remain available.
4. Remove write access to this repository from ordinary users. Keep it for the people who approve the `swarm` environment.
5. Decide whether to delete the root `usr_code.py` that a previous queue push left on `main`. This proposal does not delete it.

Until step 2, both paths are live and they do not share a queue. That is intentional. Turning the poller off is a lab-computer change, not a git change.

## Security

The dangerous combination is a self-hosted runner plus a workflow file that a stranger can edit. A pull request from a fork runs the workflow file *from the fork*. If that file said `runs-on: [self-hosted, coachbot-lab]`, and the repository allowed fork workflows on self-hosted runners, the lab computer would execute it.

What this proposal does instead:

- Validation is `pull_request` on `ubuntu-24.04`. User code runs there, inside the simulator, because that is the check. The child process environment has no `GITHUB_TOKEN`. The runner is GitHub's, not the lab's.
- The hardware workflow is `pull_request_target` plus the `hardware-run` label. `pull_request_target` runs the workflow file from the default branch. A fork cannot change it by editing the pull request. The fork author also cannot add a label on this repository.
- The hardware job has `environment: swarm`. GitHub holds the job until a required reviewer approves. No step, including checkout, is sent to the lab runner before that.
- Before approval, a GitHub-hosted prepare job only reads the list of changed files and writes a commit SHA and a folder name. It does not check out the pull request.
- After approval, the lab job checks out the trusted scripts from the default branch and the pull-request files into a separate directory. It runs the trusted script and passes the folder in as data.
- `workflow_dispatch` is restricted to `refs/heads/main` inside the workflow file that lives on `main`.

That last guard does not survive a person who can push a different workflow to a branch and dispatch it. Write access to this repository is the boundary. The environment and the label are the day-to-day gates for people who can only open pull requests.

The comment posted from `workflow_run` treats the artifact as data. It ignores any pull-request number stored in the artifact and looks up the open pull request whose head commit is the commit GitHub recorded for the run. Check details are placed in a code fence with `@` removed, so a log cannot mention someone into a notification. A summary that is not version 1 is not rendered.

## Public or private

| | Public repository | Private repository |
| --- | --- | --- |
| Who can see code and videos | Anyone who can open the pull request. Artifacts on a public repository are visible with the run | Organization members, plus outside collaborators you add |
| Fork pull requests from researchers you do not know | Yes. This is the path the workflows are built for | Outside collaborators need access. Forks of private repositories are a poorer fit for one-off academic users |
| Required reviewers on an environment | Available on the free plan for public repositories | GitHub Team or Enterprise. The free plan's required-reviewer rule does not apply to private repositories |
| GitHub Education | Not required for the public design | A school can often get GitHub Team at no cost through GitHub Education. That has to be confirmed for this org before a private repository is the chosen design |
| Self-hosted runner | Safe only with the rules above, and with fork self-hosted workflows left disabled | A private repository reduces strangers, and it does not remove the need for the environment gate |

If results must stay private between users, a public pull request is the wrong place for the video. A private repository, or a public code review followed by a private artifact store, is a product decision. The workflows upload artifacts either way. They do not implement per-user privacy inside a public repository.

## Questions still open

1. Does any course use GitHub Classroom to create the per-user repositories, or are those repositories created by hand after the email to `coachbotswarmsystem@gmail.com`? The user guide does not mention Classroom.
2. Where is the polling script, and who can change it? The hardware job needs it, or a thin adapter, to accept `--submission-dir`, `--output-dir`, and `--timeout-sec`.
3. Should videos and logs stay visible only to the submitting user? That decides public versus private, and it decides whether GitHub Team has to be confirmed through GitHub Education.
4. Who should own the Coachbot-Swarm organization from here on, so it is not a single student account?
5. The guide links `michelleezhang/swarm_simulation`. These checks pin `michelleezhang/swarm-simulator` at `25c8ea3`, which is the tree that matches `coachbot_simulator.py -u -c -i`. Which one should the lab pin?
6. The simulator listens on TCP port 8000 and has no config key for that port. Is that acceptable on the lab computer and on GitHub-hosted runners, or should upstream grow a port setting?
7. Is a pull-request comment an acceptable replacement for the three emails, or does the lab script still need to send mail?

## The orbit example

[submissions/example-orbit](../submissions/example-orbit) is the known-good input.

- Robot 0 starts at `(0, 0)`, holds, lights blue, and broadcasts `struct.pack("ff", x, y)` every loop.
- Robot 1 starts at `(0.8, 0)`, facing `+y`, and orbits the broadcast pose. In a 60 second headless run it settles near 0.53 m. The 3 cm gap is the proportional controller.
- The LED on robot 1 changes with the angle around robot 0.
- `set_vel` is sent on every loop, and both wheels are clamped to ±50.

A local headless run, from a clone of the pinned simulator, with `USE_VIS` set to 0:

```bash
python3 -m coachbot_ci.simulate fetch third_party/swarm-simulator
SIMULATOR_DIR=third_party/swarm-simulator .venv/bin/python -m unittest tests.test_simulate -v
```

The full 60 second check is what the pull-request workflow runs. The unit test uses 8 simulated seconds, which is enough to see robot 0 stay put and robot 1 start inward.

## Running the checks on a laptop

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-simulator.txt
# Either of these satisfies the Python 2.7 syntax check:
#   export PY2_BIN=/path/to/python2.7
#   docker pull python:2.7.18-slim
.venv/bin/python -m unittest discover -s tests -v
SKIP_SIM=1 .venv/bin/python -m coachbot_ci.validate --root .
```

`actionlint` should be clean on the three workflow files. The hardware workflow cannot be proven on the lab runner from this repository: there is no runner and no private script here. A missing `COACHBOT_RUN_SCRIPT` fails closed and writes `NOT_CONFIGURED.txt`.
