package acme;
class Box { int v; }
class Ord {
  void go(Box b) {
    int x = b.v;
    b.v = x + 1;
  }
}
