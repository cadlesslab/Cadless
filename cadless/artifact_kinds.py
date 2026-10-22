"""The artifact kinds a build writes, named apart from the code that writes them.

Kept free of any import so that reading them pulls in no engine configuration:
a catalog manifest's checks need these names, and reading a manifest must not
need an engine configured to run.
"""

#: The formats every part of a build is exported to, one file per part each.
PART_KINDS = ("step", "glb", "stl", "obj")

#: The whole-assembly files, keyed by artifact kind. Not part kinds: each is one
#: file for the whole build, not one per part, so the per-part naming does not
#: describe it.
ASSEMBLY_FILES = {"assembly_glb": "assembly.glb", "assembly_step": "assembly.step"}
