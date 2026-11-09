"""Small helper shared by the checks that group accounts together."""


class Groups:
    """Union-find: join accounts that are linked, then ask which group each one ended up in.

    Every account points to a "parent". Following the parents up you reach the
    group's root, and two accounts are in the same group if they reach the same
    root. Joining two groups is just pointing one root at the other.

    The nice part is that links chain for free: join(A, B) then join(B, C) and
    A, B and C all end up under one root, without ever comparing A to C.
    """

    def __init__(self):
        self.parent = {}  # account -> its parent; a root is its own parent

    def find(self, x):
        """The root of x's group. Adds x as its own group if it's new."""
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            # shortcut: point x at its grandparent on the way up, so the
            # chains stay short and the next find() is faster
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def join(self, a, b):
        """Put a and b (and everything already linked to them) in one group."""
        self.parent[self.find(a)] = self.find(b)

    def join_all(self, items):
        """Put all items in one group. An empty list does nothing."""
        if not items:
            return
        first, *rest = items
        for x in rest:
            self.join(first, x)

    def members(self):
        """root -> set of everything in that group."""
        out = {}
        for x in self.parent:
            out.setdefault(self.find(x), set()).add(x)
        return out