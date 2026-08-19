package acme;
public class Shop {
  public double make(){ double amt = 50.0; Invoice inv = new Invoice(amt); return inv.getAmount(); }
}
