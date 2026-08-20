# PLATea+
Track
# Group Name: PLATea+ (Platypus)
Group Member:
+ Nam Anh Trinh
+ Ha Linh Nguyen
+ Ha Phuong Nguyen
+ Phuong Trang Tran
# Obsidio code bundle

Everything runnable for the track lives here. The prose, endpoint spec, and
scoring rules are in `OBSIDIO-DETAIL-PAGE.md` in this folder.

## What is in here

```
k6/grading.js            The load test we grade with (and you self-test with).
compose/docker-compose.yml   Optional. Only for the persistence bonus.
starters/
  node/     Node.js + Express     (server.js, package.json, Dockerfile)
  python/   Python + FastAPI      (main.py, requirements.txt, Dockerfile)
  go/       Go + net/http         (main.go, go.mod, Dockerfile)
  java/     Java + Spring Boot    (src/..., pom.xml, Dockerfile)
```

## The starters are naive on purpose

Each starter implements the full endpoint contract correctly, so it builds,
runs, and passes the health check out of the box. That clears the Docker and
plumbing hurdle so nobody loses on setup. But each one is deliberately built
WITHOUT any resilience engineering: no clustering, no worker or thread pools
sized for the box, no caching, no queueing, no load shedding. Under the load
test they will let the heavy `/risk` work clog the fast `/price` path and they
will fail the latency thresholds. Making them hold is the entire challenge.

Pick the starter for the language you know, get it running under the caps, then
run the load test and watch it struggle. That struggle is your starting line.

## Run any starter under the real caps

Build and run with the same 2 CPU / 2 GB limits the grader uses:

```
cd starters/node          # or python, go, java
docker build -t obsidio .
docker run --rm --cpus=2 --memory=2g -p 8080:8080 obsidio
```

Confirm it is alive:

```
curl http://127.0.0.1:8080/health
```

## Run the load test against it

From the k6 folder, point k6 at your running container:

```
k6 run -e TARGET=http://127.0.0.1:8080 grading.js
```

Read three things in the summary:
- `work_score`  : your weighted useful-work total (the leaderboard number).
- `http_req_failed` : your error rate (must stay under the ceiling).
- `http_req_duration{tier:price}` p95 : your fast-path latency, the headline.

The first run on a naive starter will fail the latency thresholds. That is
expected. Now go make it hold.

## Try the optional persistence setup

The compose file is a template for the persistence bonus and defaults to the
Node starter. Run it from the compose folder:

```
cd compose
docker compose up --build
```

To use another language, change the `build` path in
`compose/docker-compose.yml` to the matching directory under `starters/`.

## Submission checklist

- Submit the improved `Dockerfile` for your chosen starter.
- Submit `docker-compose.yml` only if attempting the persistence bonus.
- Include a short resilience write-up with measured k6 results.
- Include the required architecture and trade-off video pitch.

## A note on running k6 and the container together

If you run k6 and the container on the same machine, they compete for CPU and
your numbers get noisy. For casual self-testing that is fine. For numbers you
trust, give the container its 2 cores and run k6 elsewhere (another machine, or
at least cores the container is not using). The grader keeps them separate.

## Reproducible builds

All Dockerfiles pin their base image versions. Do not switch to floating
`latest` tags; the grader builds your image on its own machine and it must build
the same way it did on yours.
