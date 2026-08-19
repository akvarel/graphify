package acme;
public class Order {
  private PricingService ps;
  public double go() {
    double b = 100.0;
    double r = ps.calculate(b);
    return r;
  }
}
