# Deploying ScholarMind

The root `Dockerfile` builds one image, and one container of it runs everything (`scholarmind-run`, i.e. `deploy/run.sh`): the api (uvicorn, port 8000, serving the frontend too), the cron scheduler, one `fetch` worker and `ANALYZE_WORKERS` `analyze` workers. When any of them exits, the container exits and Docker's restart policy starts it again; `docker stop` lets each finish what it is doing. Run one such container: a second would run a second cron scheduler. To split the parts anyway, run the same image with the command `scholarmind-run api` or `scholarmind-run worker` (the worker part exactly once).

The analyze workers call the model through pi, which the image carries with Node.js (from `node:22-slim`). A `node:22-slim` stage runs `npm ci` and `npm run build` in `frontend/`, and `dist/` goes to `/app/frontend` (`FRONTEND_DIR`). The npm registries come from `NPM_REGISTRIES` in `BUILD_FLAGS`, for pi and the frontend alike. The build uses `node:22-slim` and `python:3.12-slim` as the host has them (no `--pull`); a host that cannot reach Docker Hub must have both already.

When CI passes on `main` (`lint`, `test`, and `frontend`: lint, typecheck and build with the same Node.js version as the image), the `deploy` job in `.gitea/workflows/ci.yml` streams `git archive` of the commit over SSH to `host/scholarmind-deploy.sh deploy <commit>` on the Docker host, which:

1. takes a lock, so two deploys never overlap;
2. checks the archive (plain files and directories only, nothing outside the build directory) and unpacks it;
3. builds `<IMAGE>:<commit>`; if that fails, nothing else changes;
4. runs `alembic upgrade head` in a one-off container while the old container still runs; if it fails, nothing else changes;
5. stops the old container (kept as `<name>-prev`), starts the new one, waits for `/api/health`, then checks that it keeps running, not restarted, for `SETTLE` seconds (the workers in it exit at once when they cannot start);
6. on failure, removes the new container and starts the previous one again; on success, removes the previous one and old images of `IMAGE` beyond the newest few (by ID, never `prune`).

The script trusts nothing it receives: it accepts only `deploy <commit>` and `status`, executes nothing it receives except through `docker build`, and keeps every host setting in `scholarmind-deploy.conf` on the host. `sh host/test.sh` tests it against a fake docker and `sh run_test.sh` tests `run.sh`; CI runs both.

## Prepare the Docker host

The paths and names below are examples; the deploy user (`deploy` here) must be able to run `docker`. The container joins the Docker network `scholarmind`, which must exist (`docker network create scholarmind`) with PostgreSQL and Redis on it, as the containers `postgres` and `redis` here.

1. **Data and secrets** in `/srv/scholarmind/` (create it owned by the deploy user):

   ```sh
   mkdir -p /srv/scholarmind && cd /srv/scholarmind
   chmod 700 .
   # .env: what the container gets with --env-file (no quotes, no spaces around =)
   #   DATABASE_URL=postgresql://scholarmind:<password>@postgres:5432/scholarmind
   #   REDIS_URL=redis://:<password>@redis:6379/0
   #   SECRET_KEY=<python -c "import secrets; print(secrets.token_urlsafe(48))">
   #   COOKIE_SECURE=true
   #   HTTPS_PROXY=http://proxy.example:7890
   #   LLM_API_KEY=<key of the model endpoint in analysis.base_url>
   touch .env && chmod 600 .env
   # config.yaml: from config.example.yaml, with all of its sections: arxiv, sync,
   # vocabulary (the domain and task lists briefs tag with) and analysis, e.g.
   #   analysis:
   #     base_url: https://api.example.com/v1   # an OpenAI-compatible endpoint
   #     brief_model: glm-5.3-flash
   # pi gets no proxy variables, so base_url must be reachable from the container directly.
   # The container runs as uid 10001 and reads the file through the bind mount, so it must
   # be world-readable; it holds no secrets.
   chmod 644 config.yaml
   ```

   The database and account in `DATABASE_URL` and the Redis database in `REDIS_URL` must exist first.

2. **The script** in `/srv/scholarmind/deploy/` (`STATE_DIR`: the lock, logs and build directories go there too). From a checkout of the repository:

   ```sh
   COPYFILE_DISABLE=1 tar -C deploy/host -cf - scholarmind-deploy.sh scholarmind-deploy.conf.example \
     | ssh deploy@docker-host.example 'mkdir -p /srv/scholarmind/deploy && tar -C /srv/scholarmind/deploy -xf -'
   ```

   then on the host:

   ```sh
   cd /srv/scholarmind/deploy
   cp scholarmind-deploy.conf.example scholarmind-deploy.conf
   chmod 700 scholarmind-deploy.sh && chmod 600 scholarmind-deploy.conf
   ./scholarmind-deploy.sh status     # version=none, the container missing
   ```

   The example's limits (3 CPUs, 3 GB, 2 analyze workers) are starting points. Measured idle, the api takes about 60 MB and the workers about 210 MB; each analyze worker at work adds about 0.4 GB (its job's process and the pi/Node process it starts), more once pi reads whole papers. Set them from `docker stats` while analyses run, and raise the memory with `ANALYZE_WORKERS`. The script is not updated by deploys; copy it again by hand when it changes.

3. **A key for CI**, without a passphrase, on any machine:

   ```sh
   ssh-keygen -t ed25519 -N '' -C scholarmind-deploy -f scholarmind-deploy
   ```

   Add the public key to `~/.ssh/authorized_keys` of `deploy`, one of:

   - **A. Restricted to the script (preferred):** `command="/srv/scholarmind/deploy/scholarmind-deploy.sh",restrict ssh-ed25519 AAAA... scholarmind-deploy`. Leave `DEPLOY_COMMAND` unset.
   - **B. Unrestricted**, only if A does not hold (a global `ForceCommand` in sshd overrides `command=`): `restrict ssh-ed25519 AAAA... scholarmind-deploy`, and set `DEPLOY_COMMAND` to the script's path. This key is a full login as `deploy`, which can use Docker, so effectively root on the host, and everyone who can push to the repository can get it through CI.

   Check it; with A, the last two must print the usage and exit 2 instead of running `ls`/`id`:

   ```sh
   ssh -i scholarmind-deploy deploy@docker-host.example status                                            # A
   ssh -i scholarmind-deploy deploy@docker-host.example /srv/scholarmind/deploy/scholarmind-deploy.sh status  # B
   ssh -i scholarmind-deploy deploy@docker-host.example 'ls /'
   ssh -i scholarmind-deploy deploy@docker-host.example 'status; id'
   ```

4. **Gitea secrets** of the repository (Settings → Actions → Secrets):

   | Secret | Value |
   | --- | --- |
   | `DEPLOY_SSH_KEY` | The private key file `scholarmind-deploy`, whole |
   | `DEPLOY_HOST` | `docker-host.example` |
   | `DEPLOY_USER` | `deploy` |
   | `DEPLOY_PORT` | Optional, default 22 |
   | `DEPLOY_KNOWN_HOSTS` | `ssh-keyscan -t ed25519 docker-host.example`, its fingerprint compared with `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the host |
   | `DEPLOY_COMMAND` | Only for B: `/srv/scholarmind/deploy/scholarmind-deploy.sh` |

   Without the first four the deploy job skips its steps and succeeds.

The next push to `main` deploys. The job log shows the script's output; the host keeps it in `STATE_DIR/deploy.log`, the last build log in `last-build.log` and the deployed commit in `deployed`.

## Deploy or roll back by hand

- **From CI:** run the `ci` workflow by hand with `ref` set to a commit, tag or branch. It skips the checks and deploys that ref; use it for commits that passed CI before.
- **From your machine** with your own key (it deploys the committed tree, not uncommitted changes):

  ```sh
  git archive --format=tar 'HEAD^{tree}' \
    | ssh deploy@docker-host.example /srv/scholarmind/deploy/scholarmind-deploy.sh deploy "$(git rev-parse --short=12 HEAD)"
  ```

- **On the host** with an image that is still kept (`docker images scholarmind`), without rebuilding:

  ```sh
  cd /srv/scholarmind/deploy && . ./scholarmind-deploy.conf && v=<commit>
  docker rm -f "$CONTAINER"
  docker run -d --name "$CONTAINER" $RUN_FLAGS $CONTAINER_FLAGS "$IMAGE:$v"
  ```

## Not covered

- Migrations are not rolled back: a rollback runs the previous image on the migrated database. The script does not back up PostgreSQL.
- The image is built on the deploy host, not in CI, so a broken `Dockerfile` shows up as a failed deploy (the running container stays).
- Whoever can push to `main` decides what is built and run on the host.
