# syn

A command line toolbox that reads and changes Salesforce metadata with far less text than `cat`, `grep`
and hand-edited XML. There is no model inside: it is plain code, made to be called from a terminal by a
person or by a coding agent of any harness.

## Install

Windows, no Python needed:

```powershell
irm https://raw.githubusercontent.com/Dyarchia/syn/main/install.ps1 | iex
```

It unpacks the latest release into `%LOCALAPPDATA%\Programs\syn` and adds that folder to the user PATH.
Run it again to update. Only Windows builds are published and tested so far.

From source, with Python 3.12 or newer and no dependencies:

```bash
py -m pip install -e .
```

## Commands

Run them from the root of an SFDX project. `syn COMMAND --help` gives the exact syntax.

```text
Command                       What it prints or does
----------------------------  ----------------------------------------------------------------
syn map                       One-screen overview of the repository
syn find NAME                 Where a name is defined and who mentions it, grouped by type
syn rows FILE [BLOCK]         The repeated blocks of a metadata XML file as a table
syn set FILE BLOCK KEY F=V    Change fields of one row; only the bytes of that row change
syn add FILE BLOCK F=V        Add a row
syn del FILE BLOCK KEY        Remove a row
syn flow FILE                 A Flow as an outline in execution order
syn diff                      What changed in the meaning of metadata files, without XML noise
syn tests FILE                Condense the JSON of `sf apex run test --json`
syn errors FILE               Condense the JSON of `sf project deploy start --json`
syn log FILE                  Condense an Apex debug log
syn index                     Rebuild the name index from scratch
syn org                       One line per authenticated org
```

- `FILE` may be a quoted glob: one command reads or changes many profiles or permission sets.
- `find` and `map` keep an index in `.syn/` at the repository root; add it to `.gitignore`.
- `tests` and `errors` read JSON that the Salesforce CLI (`sf`) wrote, and `org` asks `sf` for its list of
  orgs. No command deploys or changes anything in an org.

## Tell an agent about it

Put `syn` on the agent's PATH and add a few lines to its instructions file: that the command exists, the
list above, and to prefer it over reading XML.

## Tests

```bash
py -m pytest -q
```

## License

Apache-2.0. See `LICENSE`.
