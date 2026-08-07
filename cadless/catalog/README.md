# Catalog CLI — reading what was authored elsewhere

`python -m cadless.catalog <command>` operates on catalog content under
`CADLESS_CATALOG_ROOT` (default `./catalog`, one subdir per domain:
`house-catalog/`, `mech-catalog/`, `furniture-catalog/`, `fixture-catalog/`).
The bundled catalog is committed there so the tool works on a fresh clone;
point `CADLESS_CATALOG_ROOT` elsewhere to read your own content.

| Command | What it does |
|---|---|
| `load`, `reload` | Read item directories and write them into the live database |
| `clear` | Remove loaded items from that database |
| `list` | Show which discovered items are loaded |

**Authoring is not here.** Turning source material into a catalog item —
parsing floor plans or CAD records, generating step code, executing it into
baked artifacts, scoring the result — runs in a private pipeline. This tool
consumes what that produces. The catalog root is bind-mounted into the docker
containers read-only (`docker-compose.yml` mounts it with `:ro`), and nothing
here writes to it.

One writer does run container-side, and it does not contradict that — it writes
to the data volume the containers own, never to the `:ro` mount:

- `POST /packages/import` takes a received `.cls` into the catalog, under
  `$CADLESS_DATA_DIR/imported-catalog/`. What a user receives is their own data
  and lands with the rest of it.

Loading the bundled mechanical catalog into a running stack:

```bash
docker exec cadless-api-1 python -m cadless.catalog load --all \
  --catalog-dir /catalog/mech-catalog
```
