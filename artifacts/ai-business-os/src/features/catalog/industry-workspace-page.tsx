import { useState } from "react";
import { Check, Package, Plus } from "lucide-react";
import { useBusiness } from "@/business-context";
import {
  Badge,
  Button,
  Card,
  Modal,
  PageHeader,
} from "@/components/product-ui";
import { money } from "@/lib/product-utils";

export function IndustryWorkspacePage() {
  const { activeBusiness, updateBusiness } = useBusiness();
  const [adding, setAdding] = useState(false);
  const industry = activeBusiness?.industry ?? "Other";
  const isRealEstate = industry === "Real Estate";
  const title = isRealEstate
    ? "Properties & listings"
    : industry === "E-commerce"
      ? "Products"
      : "Inventory & harvest";
  const eyebrow = isRealEstate
    ? "Real estate workspace"
    : industry === "E-commerce"
      ? "Commerce workspace"
      : "Operations workspace";

  return (
    <>
      <PageHeader
        eyebrow={eyebrow}
        title={title}
        subtitle={`Keep ${activeBusiness?.name ?? "your business"} moving with a clear view of what is available.`}
        action={
          <Button
            variant="primary"
            onClick={() => setAdding(true)}
            data-testid="button-add-industry-item"
          >
            <Plus /> Add {isRealEstate ? "listing" : "item"}
          </Button>
        }
      />
      <Card className="table-card" pad={false}>
        <div className="table-toolbar">
          <div>
            <div className="eyebrow">Live catalog</div>
            <h2>{activeBusiness?.products.length ?? 0} active items</h2>
          </div>
          <Badge tone="success">Synced to AI team</Badge>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{isRealEstate ? "Listing" : "Name"}</th>
                <th>Price</th>
                <th>Availability</th>
                <th>AI readiness</th>
              </tr>
            </thead>
            <tbody>
              {(activeBusiness?.products ?? []).map((product) => (
                <tr key={product.id}>
                  <td>
                    <strong>{product.name}</strong>
                  </td>
                  <td>{money(product.price)}</td>
                  <td>
                    <Badge
                      tone={
                        product.availability.toLowerCase().includes("stock") ||
                        product.availability.toLowerCase().includes("available")
                          ? "success"
                          : "warning"
                      }
                    >
                      {product.availability}
                    </Badge>
                  </td>
                  <td>
                    <Badge tone="info">
                      <Check size={12} /> Known by AI team
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!activeBusiness?.products.length && (
            <div className="empty">
              <Package />
              <h3>No items configured</h3>
              <p>
                Add products or services from onboarding or Settings to give
                your AI team context.
              </p>
            </div>
          )}
        </div>
      </Card>
      {adding && activeBusiness && (
        <Modal
          title={`Add ${isRealEstate ? "listing" : "item"}`}
          description={`Add this directly to ${activeBusiness.name}'s catalog and Business Brain context.`}
          onClose={() => setAdding(false)}
        >
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              void updateBusiness(activeBusiness.id, {
                ...activeBusiness,
                products: [
                  ...activeBusiness.products,
                  {
                    id: `item-${Date.now()}`,
                    name: String(form.get("name")),
                    price: Number(form.get("price")),
                    availability: String(form.get("availability")),
                  },
                ],
              }).then(() => setAdding(false));
            }}
          >
            <div className="form-grid">
              <div className="field full">
                <label>{isRealEstate ? "Listing name" : "Item name"}</label>
                <input name="name" required autoFocus />
              </div>
              <div className="field">
                <label>Price</label>
                <input name="price" type="number" min="0" required />
              </div>
              <div className="field">
                <label>Availability</label>
                <input
                  name="availability"
                  required
                  defaultValue={isRealEstate ? "Available" : "In stock"}
                />
              </div>
            </div>
            <div className="modal-foot">
              <Button type="button" onClick={() => setAdding(false)}>
                Cancel
              </Button>
              <Button variant="primary" type="submit">
                Add {isRealEstate ? "listing" : "item"}
              </Button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
