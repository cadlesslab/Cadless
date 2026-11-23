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
check keys, and it can be copied into another codebase as it is, on Python 3.12
or later (only `toolchain_identity` needs more: the engine files beside it, and
build123d and OCP installed). Call it rather than writing your own. The rules
below are what it does, written down so that they can be reviewed, and pinned by
the shared vectors in `cadless/item_key_vectors.json`, which hold leaf cases for
Python 3.12, 3.13 and 3.14.

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
  `1e+21`, `1e-7` is `1e-7`, and `-0.0` is `0`. A JSON number is a double, so an
  integer past +/-(2**53 - 1) is accepted only when a double holds it exactly, and
  is written as that double is (`2**60` as `1152921504606847000`).
- An instance of a number or string subclass (an `IntEnum` member, a numpy
  `float64`, a `str` subclass) is written as the plain value it stands for,
  whatever its own `repr` says. Other numpy types are not numbers here.
- Object members are sorted by the UTF-16 code units of their keys, which differs
  from code point order above U+FFFF.
- Strings escape only what JSON requires; nothing is written between tokens.
- Refused: NaN and infinities, an integer no double holds exactly, a non-string
  key, two keys that are the same string, a string holding a lone surrogate,
  nesting deeper than 100 levels (a container that holds itself among them), and
  any other type.

## Params

Every parameter declares one of four types: `float` (lengths and angles), `int`
(counts), `str` or `bool`. Each value is converted to its declared type before it
is keyed, and `typed_params` returns the converted values: the ones a recipe must
run with, which whatever runs a keyed leaf hands to the engine.

- The names in `params` must be exactly the declared names.
- An `int` is accepted where a `float` is declared. An integral float such as
  `2.0` is accepted where an `int` is declared; `2.5` is refused.
- A `bool` is never taken for a number, and a number never for a `bool`.
- NaN and infinities are refused, as is an `int` too large for a float where a
  float is declared.
- A float negative zero becomes zero. Canonical JSON writes both as `0`, and a
  recipe must run with the value its key holds: `atan2(-0.0, -1)` is not
  `atan2(0.0, -1)`.
- Each value comes back as a plain `float`, `int`, `str` or `bool`.
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
   normalised. Its length is taken with `math.fsum`, which is exactly rounded, so
   it is the same on every interpreter.
2. Every number is rounded to six decimal places, half to even on the exact
   double (Python's `round`). This matters only at exact ties: `0.0078125` rounds
   to `0.007812`, where a half-up rounding gives `0.007813`.
3. The sign is fixed on the rounded quaternion: `qw` positive, or, where `qw`
   rounds to zero, the first non-zero of `qx`, `qy`, `qz`. So `q` and `-q` give one
   pose however close `qw` lies to zero.
4. Negative zero is written as zero.

A quaternion already on the six-decimal grid and within 2e-6 of unit length is
kept as it is, apart from the sign. Rounding a unit quaternion moves its length by
at most 1e-6, and normalising the rounded one again could move its last digit, so
without this rule about one canonical pose in a hundred would change when
canonicalised a second time. With it, a canonical pose is its own canonical form,
and so is its negation.

## Recipes

`canonical_recipe` parses the recipe and writes it back with Python's
`ast.unparse`, so comments and layout drop out. Every docstring is dropped too
(module, class and function), and a body left empty keeps a `pass`. A
docstring cannot change what a recipe builds, since the code gate refuses
`__doc__` and every other dunder name.

A recipe binds `params` exactly once, as a plain dict literal at module level
from distinct string names to numbers, strings or booleans, and binds it nowhere
else: no second assignment, `|=`, loop or `with` target, walrus, `del`, argument,
`global` or `nonlocal` declaration, `def`, `class`, import, `except` or `match`
capture, or type parameter of that name, in any scope. A later change through the
literal, such as `params['w'] = 2`, is not a binding and stays in the text. That
literal stays where it stands, its names in their order and each value written as
`...`:

```python
params = {'width': ..., 'height': ..., 'label': ...}
```

The values are keyed through the params, so two items sharing a recipe share its
canonical text. The literal's place and the order of its names stay in the text
because they can change what a recipe does: a statement above the literal runs
before `params` exists, and a recipe can loop over `params`.

Refused: a recipe with no `params` literal, or with two; one inside a block; an
annotated, unpacked, starred or chained `params`, or any other binding of it; a
literal that unpacks another mapping, names one parameter twice, has a name that
is not a string, or a value that is not a number, string or boolean (a list or a
tuple is refused, so write `hole_offset_1` and `hole_offset_2` rather than one
list); a source Python cannot parse; syntax nested deeper than 100 levels; an
integer literal of more than 640 digits (the lowest limit a process can set on
how many digits the interpreter writes, so below it the text cannot depend on
that setting); and a recipe whose canonical text would not read back as the same
program. `ast.unparse` writes a few constructs so that they read back as others (a
`}` in an f-string format spec reads back as an empty spec and a literal brace; a
0x00 byte there is written as it is and does not read back at all), and two
recipes must never share a text while doing different things, so the canonical
text is parsed again and compared.

How deep a recipe or a value may nest is a fixed limit rather than the
interpreter's, so a caller a few hundred calls deep gets the same answer as one at
the top. A caller deeper still is refused, and only one at the interpreter's very
limit, with no call left to make, gets the interpreter's own RecursionError.

A syntax warning (an unknown escape such as `'\d'`, for one) changes nothing: it is
ignored, so a process that turns warnings into errors gets the same text.

`ast.unparse` belongs to the interpreter, so the canonical text is defined per
Python minor version, which is why that version is part of the toolchain and why
a leaf key is computed on the Python its toolchain names.

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
| `build123d`, `ocp` | the one installed distribution providing each kernel module, and its version, read from package metadata without importing the kernel; refused unless the file Python would import the module from is one that distribution installed |
| `python` | major.minor |
| `platform` | `sys.platform` and the machine |
| `runtime` | the sha256 of the canonical JSON mapping each of `RUN_AND_BAKE_FILES` to the sha256 of its bytes: the whole engine files that run a recipe and bake its solid (the runtime and its allow-list, the code that writes the declared values into the recipe, the worker that starts the run, the child that bakes it, and the tree that places and scales a build's parts), read from beside the module as imported |
| `helpers` | the same over the `HELPER_SOURCES` files of the helpers named, or `-` for none |
| `fonts` | `-`: no fonts ship for recipes, so `leaf_key` refuses a recipe that uses `Text`, `DimensionLine`, `ExtensionLine` or `TechnicalDrawing` (by name, as an attribute such as `build123d.Text`, or through an import under another name), or a `make_text`, `make_triad` or `symbol` attribute: its solid would depend on whichever font the machine finds. `make_triad` and the symbol of a rigid or ball joint draw letters in the font build123d bundles, which it registers without overriding a font of that family already installed, so what they draw depends on the machine as well; every `symbol` is refused, since the name cannot say whose it is |

There is no engine version number. A change to the engine's own code that can
alter a shape changes a hashed file, and a kernel upgrade changes a kernel part,
giving every leaf a new key. A test fails when a hashed file starts importing an
engine module that is neither hashed nor known to leave solids alone.

Outside the toolchain, on purpose: the other packages build123d builds geometry
with (scipy, sympy and others), which an engine release pins in its constraints
but does not hash, so a release that changes only them keeps every key; the code
gate, which refuses a recipe or lets it run but never shapes it; and the
exporters. A key names the solid, so a file exported from it in some format
depends on that format's exporter as well, and is keyed with it by whatever stores
it.

## Leaf keys

`leaf_key(source, params, types, toolchain)` takes the toolchain as text, so a key
can be computed or checked on another machine with the same Python minor version
as the one that built the leaf. Pass the string
`toolchain_identity(helpers_called(source))` returned where the leaf was built.
Beyond the refusals of its parts, it refuses:

- declared names that are not the names in the recipe's `params` literal: an
  undeclared name would run with the literal's value, which no part of the key
  holds;
- a toolchain whose `helpers` part disagrees with whether the recipe calls a
  helper, so that one leaf has one key;
- a toolchain naming another Python than the one running, whose `ast.unparse`
  could write the recipe otherwise;
- a recipe that draws text, until fonts ship with the engine and are hashed.

### What a keyed build relies on

A key holds the recipe, the declared values and the toolchain, so a build is
sound only if nothing else reaches the solid. Whatever runs a leaf must:

- run the recipe with the values `typed_params` returns written into its params
  literal, exactly there and nowhere else;
- run it with a fixed string hash seed and none of the interpreter settings that
  can change what a script does: a recipe may iterate a set of strings, whose
  order follows the seed, hold an `assert` (`-O` strips it), take `str()` of a
  large integer (the digit limit decides whether that raises), or hold a syntax
  warning (a warning filter can make it an error).

The engine provides the means for both, and no engine code keys leaves yet:
`apply_param_overrides` writes the values it is handed exactly into the literal,
finding it by the lines and columns Python itself counts, so handing it the
values `typed_params` returns is the caller's part; and the worker starts every
build's child with `PYTHONHASHSEED=0`, in UTF-8 mode, and with no other
`PYTHON*` setting from its environment beyond where Python, its packages and its
bytecode live.
Both files are among `RUN_AND_BAKE_FILES`, so a change to either changes every
key.

A recipe must also not depend on object identity, which differs from one run to
the next whatever the hash seed: `repr()` of a function shows its address, and a
set of shapes, which hash by identity, orders them differently each time. The
closed-recipe rules are to refuse that; until they do, such a recipe is a way
for a leaf to build differently under one key.

## Item keys

`item_key(document)` keys an item document as canonical JSON, without its own
top-level `key` field, so a document can carry its key. List each member with its
key and its pose as `canonical_pose` gives it: the item's key then changes
whenever a member's does, to any depth, while a member's own `key` field stays in.

## Closed recipes

The closed-recipe rules are the allow-list a recipe will have to pass before it
is keyed; apart from the text rule, which `leaf_key` applies now, they are not
enforced yet. Keys assume what they will require:

- leaves are in millimetres; an item from a metre domain is converted where it
  enters;
- `math` is allowed beside build123d;
- text is drawn only in fonts the engine ships, and none ship yet;
- nothing depends on object identity (`repr()` of a function or an object, `id`,
  the order of a set of shapes).

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
Leaf cases name their toolchain as text, so their keys do not depend on the rest
of the machine; a leaf case with a `python` field applies on that Python minor
version only, and every supported version has its own. `tests/test_item_key.py`
runs every case for the Python running it; code that calls this module can run
them too:

```python
import json, sys
from importlib import resources

from cadless import item_key

vectors = json.loads(resources.files("cadless").joinpath("item_key_vectors.json").read_text())
assert vectors["definitions"] == item_key.DEFINITIONS_VERSION
running = f"{sys.version_info.major}.{sys.version_info.minor}"
for case in vectors["leaf"]:
    if not case.get("refused") and case.get("python", running) == running:
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
