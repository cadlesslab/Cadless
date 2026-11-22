# Item keys

An item key names a part or an assembly by what it is, so that the same item gets
the same key wherever it is built and a changed item gets a new one. There are two
kinds:

- a **leaf key** names one solid built by a recipe: a build123d script with its
  editable dimensions in one `params = {...}` literal;
- an **item key** names an item with members: other items placed by a pose, each
  named by its own key, to any depth.

**There is one implementation: `cadless/item_key.py`.** It imports nothing but the
standard library, so code without the geometry kernel installed can compute and
check keys, and it can be copied into another codebase as it is (only
`toolchain_identity` needs the engine files beside it). Call it rather than
writing your own. The rules below are what it does, written down so that they can
be reviewed, and pinned by the shared vectors in `cadless/item_key_vectors.json`.

These are **definitions version 1** (`DEFINITIONS_VERSION`).

## What each key covers

| Key | The sha256, as lowercase hex, of |
| --- | --- |
| Leaf | the canonical recipe, a 0x00 byte, the canonical params, a 0x00 byte, the toolchain |
| Item | the canonical JSON of the item document, its own top-level `key` field left out |

None of the three parts of a leaf key holds a 0x00 byte (a recipe or toolchain
that would is refused), so no two different inputs share a preimage. A leaf
preimage always holds two 0x00 bytes and canonical JSON never holds one, so a leaf
key and an item key cannot share one either.

## Canonical JSON

Every structured input is written as RFC 8785 canonical JSON (the JSON
Canonicalization Scheme), implemented in the module rather than taken from a
dependency, so that no third-party release can change a key.

- Numbers take their shortest ECMAScript form: `2.0` is written `2`, `1e21` is
  `1e+21`, `1e-7` is `1e-7`, and `-0.0` is `0`.
- Object members are sorted by the UTF-16 code units of their keys, which differs
  from code point order above U+FFFF.
- Strings escape only what JSON requires; nothing is written between tokens.
- Refused: NaN and infinities, an integer past +/-(2**53 - 1) (a JSON reader cannot
  get it back exactly), a non-string key, a string holding a lone surrogate, and
  any other type.

## Params

Every parameter declares one of four types: `float` (lengths and angles), `int`
(counts), `str` or `bool`. Each value is converted to its declared type before it
is keyed and before the recipe runs with it (`typed_params` returns those values).

- The names in `params` must be exactly the declared names.
- An `int` is accepted where a `float` is declared. An integral float such as
  `2.0` is accepted where an `int` is declared; `2.5` is refused.
- A `bool` is never taken for a number, and a number never for a `bool`.
- NaN and infinities are refused.
- Strings are keyed as given. They are not Unicode-normalised, so a precomposed
  and a decomposed letter give different keys.

The canonical params are the canonical JSON of each name mapped to its declared
type and its value: `{"a":{"type":"float","value":1.5},"b":{"type":"int","value":2}}`.
The type is written beside the value because canonical JSON writes `4` and `4.0`
alike.

## Poses

A pose is seven numbers: a translation in millimetres and a quaternion,
`(x, y, z, qx, qy, qz, qw)`. `canonical_pose` gives its one form:

1. A quaternion further than 1e-6 from unit length is refused; one within it is
   normalised.
2. Every number is rounded to six decimal places, half to even on the exact
   double (Python's `round`). This matters only at exact ties: `0.0078125` rounds
   to `0.007812`, where a half-up rounding gives `0.007813`.
3. The sign is fixed on the rounded quaternion: `qw` positive, or, where `qw`
   rounds to zero, the first non-zero of `qx`, `qy`, `qz`. So `q` and `-q` give one
   pose however close `qw` lies to zero.
4. Negative zero is written as zero.

## Recipes

`canonical_recipe` parses the recipe and writes it back with Python's
`ast.unparse`, so comments and layout drop out. Every docstring is dropped too
(module, class and function), and a body left empty keeps a `pass`. A
docstring cannot change what a recipe builds, since the code gate refuses
`__doc__` and every other dunder name.

A recipe binds `params` exactly once at module level, as a plain dict literal from
string names to numbers, strings or booleans. That literal stays where it stands,
its names in their order and each value written as `...`:

```python
params = {'width': ..., 'height': ..., 'label': ...}
```

The values are keyed through the params, so two items sharing a recipe share its
canonical text. The literal's place and the order of its names stay in the text
because they can change what a recipe does: a statement above the literal runs
before `params` exists, and a recipe can loop over `params`.

Refused: a recipe with no `params` literal, or with two; one inside a block; an
annotated, unpacked, starred or chained `params`; a literal whose names are not
strings, or whose values are not numbers, strings or booleans (a list or a tuple
is refused, so write `hole_offset_1` and `hole_offset_2` rather than one list); a
source Python cannot parse, or that is nested too deeply to write back; and a
recipe whose canonical text would hold a 0x00 byte (which `ast.unparse` writes as
it is inside an f-string format spec).

`ast.unparse` belongs to the interpreter, so the canonical text is defined per
Python minor version, which is why that version is part of the toolchain.

### Helpers

A recipe is handed engine helpers rather than importing them; today the only one
is `split_and_join`. `helpers_called` names the helpers a recipe loads by their
bare name. A load counts even where the recipe binds that name itself: telling a
recipe's own function from the engine's is not worth the risk of leaving a
helper's source out of its key. `HELPER_SOURCES` lists, for each helper, the files
whose bytes key a leaf that calls it: the helper's module and every engine module
it imports.

## The toolchain

`toolchain_identity(helpers)` names what builds a leaf on this machine:

```text
build123d=build123d==0.11.1;ocp=cadquery-ocp-novtk==7.9.3.1.1;python=3.12;
platform=linux-x86_64;runtime=<sha256>;helpers=<sha256 or ->;fonts=-
```

(on one line). The parts:

| Part | What it holds |
| --- | --- |
| `build123d`, `ocp` | the one installed distribution providing each kernel module, and its version, read from package metadata without importing the kernel |
| `python` | major.minor |
| `platform` | `sys.platform` and the machine |
| `runtime` | the sha256 of the canonical JSON mapping each of `RUN_AND_BAKE_FILES` to the sha256 of its bytes: the whole engine files that run a recipe and bake its solid |
| `helpers` | the same over the `HELPER_SOURCES` files of the helpers named, or `-` for none |
| `fonts` | `-`: no fonts ship for recipes, so a recipe draws no text |

There is no engine version number. A change that can alter a shape changes a
hashed file or a kernel version instead, and a release that changes neither keeps
every key. A kernel upgrade gives every leaf a new key.

Outside the toolchain, on purpose: the other packages build123d depends on, which
an engine release pins in its constraints; and the exporters. A key names the
solid, so a file exported from it in some format depends on that format's exporter
as well, and is keyed with it by whatever stores it.

## Leaf keys

`leaf_key(source, params, types, toolchain)` takes the toolchain as text, so a key
can be computed or checked away from the machine that built the leaf. Pass the
string `toolchain_identity(helpers_called(source))` returned where the leaf was
built. Beyond the refusals of its parts, it refuses:

- declared names that are not the names in the recipe's `params` literal: an
  undeclared name would run with the literal's value, which no part of the key
  holds;
- a toolchain whose `helpers` part disagrees with whether the recipe calls a
  helper, so that one leaf has one key.

## Item keys

`item_key(document)` keys an item document as canonical JSON, without its own
top-level `key` field, so a document can carry its key. List each member with its
key and its pose as `canonical_pose` gives it: the item's key then changes
whenever a member's does, to any depth, while a member's own `key` field stays in.

## Closed recipes

Keys assume what the closed-recipe rules will require of a leaf:

- leaves are in millimetres; an item from a metre domain is converted where it
  enters;
- `math` is allowed beside build123d;
- text is drawn only in fonts the engine ships, and none ship yet.

## The API

| Name | What it gives |
| --- | --- |
| `DEFINITIONS_VERSION` | the version of these definitions |
| `ItemKeyError` | what every refusal raises (a `ValueError`) |
| `jcs(value)` | canonical JSON, as UTF-8 bytes |
| `typed_params(params, types)` | the values converted to their declared types |
| `canonical_params(params, types)` | the canonical params, as bytes |
| `canonical_pose(pose)` | the seven numbers in canonical form |
| `canonical_recipe(source)` | the canonical recipe text, as UTF-8 bytes |
| `helpers_called(source)` | the helpers a recipe loads |
| `toolchain_identity(helpers)` | the toolchain text for this machine |
| `leaf_key(source, params, types, toolchain)` | a leaf's key |
| `item_key(document)` | an item's key |
| `PARAM_TYPES`, `HELPER_SOURCES`, `RUN_AND_BAKE_FILES` | the constants above |

## The vectors

`cadless/item_key_vectors.json` ships inside the package, so it arrives with the
wheel. It records `definitions`, then cases in six sections (`jcs`, `params`,
`pose`, `recipe`, `leaf`, `item`). Each case gives its input and either its
outcome or `"refused": true`; numbers given as `bits` are IEEE 754 doubles in hex.
Leaf cases name their toolchain as text, so their keys do not depend on the
machine. `tests/test_item_key.py` runs every case; code that calls this module can
run them too:

```python
import json
from importlib import resources

from cadless import item_key

vectors = json.loads(resources.files("cadless").joinpath("item_key_vectors.json").read_text())
assert vectors["definitions"] == item_key.DEFINITIONS_VERSION
for case in vectors["leaf"]:
    if not case.get("refused"):
        args = (case["source"], case["params"], case["types"], case["toolchain"])
        assert item_key.leaf_key(*args) == case["key"]
```

## Changing the definitions

Any change to a canonical form, a toolchain part or a key's preimage changes keys,
so it ships as a new definitions version, never inside the current one:

- [ ] Raise `DEFINITIONS_VERSION` and the vector file's `definitions` together.
- [ ] Change the rules here and in the module in the same pull request.
- [ ] Update the outcomes the change moves, and check them against an
      independent implementation of canonical JSON and sha256.
- [ ] Say in the release notes that keys of the old version are not comparable
      with the new ones, so stored items need re-keying.

Adding a helper is not a definitions change, but it needs its `HELPER_SOURCES`
entry; the tests fail until it has one.
