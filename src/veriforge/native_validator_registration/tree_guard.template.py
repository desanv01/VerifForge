def _check_tree(root, pins, *, exact=False, limit=1048576, error_type=ValueError):
    """Fresh no-follow walk + byte hashing, without per-file deep resolve calls."""
    def fail():
        raise error_type('Native validator source or dependency inventory differs')
    if root.is_symlink() or root.is_junction() or not root.is_dir():
        fail()
    directories = {''}
    for name in pins:
        parts = name.split('/')
        if not name or '\\' in name or ':' in name or any(x in ('','.','..') for x in parts):
            fail()
        for index in range(1, len(parts)):
            directories.add('/'.join(parts[:index]))
    seen, pending = set(), [(root, '')]
    while pending:
        directory, prefix = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                name = prefix + entry.name
                relevant = exact or name in directories or name in pins
                if not relevant:
                    continue
                path = Path(entry.path)
                if entry.is_symlink() or path.is_junction():
                    fail()
                if entry.is_dir(follow_symlinks=False):
                    if exact and name not in directories:
                        fail()
                    pending.append((path, name + '/'))
                elif entry.is_file(follow_symlinks=False) and name in pins:
                    size = entry.stat(follow_symlinks=False).st_size
                    if size > limit:
                        fail()
                    with path.open('rb') as source:
                        raw = source.read(size + 1)
                    if len(raw) != size or hashlib.sha256(raw).hexdigest() != pins[name]:
                        fail()
                    seen.add(name)
                else:
                    fail()
    if seen != set(pins):
        fail()
