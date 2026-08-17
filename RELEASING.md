# Releasing Rundesk Google Skills

1. Put the intended package changes and one semantic `manifest.json` version bump in a pull request
   against `main`.
2. Run `python3 -m unittest discover -s tests -v` and wait for every required build check.
3. Review the manifest, OAuth profile behavior, secret redaction, bounded reads, resource selection,
   and package isolation before merging.
4. Tag the merge commit with the manifest version prefixed by `v`, then push that tag.

```sh
version=$(python3 -c 'import json; print(json.load(open("manifest.json"))["version"])')
git tag "v$version" <merge-commit>
git push origin "v$version"
```

The release automation must refuse a tag that does not match `manifest.json`, rerun the catalog
suite, and publish the GitHub Release. Never move or reuse a published tag.
