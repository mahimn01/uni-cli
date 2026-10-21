# uni

A read-only command line for your Canvas courses: what's due, your grades, announcements, and what changed.

## Install

```sh
pipx install git+https://github.com/mahimn01/uni-cli
```

## Set your token

Make a token in Canvas (Account > Settings > New access token), then run:

```sh
uni setup
```

Or set `CANVAS_BASE_URL` and `CANVAS_TOKEN` (or `CANVAS_TOKEN_FILE`, a file only you can read).

## Use

```sh
uni courses
uni due --days 7
uni grades BIO120
uni announcements --since 3d
uni changes --since 24h --json
```

Every command takes `--json`. `uni changes` prints a `cursor`; pass it back as `--since` to get only what's new.

## Read-only

uni only sends GET requests to your own Canvas site. It never submits, posts, marks anything read, or changes settings, and it never opens your inbox or quiz questions. Your token stays on your computer and goes only to Canvas. Crowdmark and Gradescope aren't supported.

## License

MIT
