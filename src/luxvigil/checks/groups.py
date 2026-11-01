"""Small helper shared by the checks that group accounts together."""


class Groups:
    """Union-find: join accounts that are linked, then ask which group each one ended up in."""

    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def join(self, a, b):
        self.parent[self.find(a)] = self.find(b)

    def join_all(self, items):
        first, *rest = items
        for x in rest:
            self.join(first, x)

    def members(self):
        """root -> set of everything in that group."""
        out = {}
        for x in self.parent:
            out.setdefault(self.find(x), set()).add(x)
        return out
