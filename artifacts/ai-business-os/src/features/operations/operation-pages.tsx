import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ArrowLeft, ArrowRight, ChevronRight, MessageCircle, Plus, Search, ShoppingBag, Users } from "lucide-react";
import { useBusiness } from "@/business-context";
import {
  getIndustryTerminology,
  getIndustryWorkspaceProfile,
} from "@/lib/industry-workspaces";
import { Avatar, Badge, Button, Card, Modal, PageHeader } from "@/components/product-ui";
import { humanizeApiError } from "@/services/api-client";
import { operationsApi } from "@/services/operations";
import type { Customer, Lead, LeadStage, Order, OrderStatus } from "@/services/api-types";

const money = (value: string | number, currency = "USD") => new Intl.NumberFormat(undefined, { style: "currency", currency }).format(Number(value));
const when = (value: string) => new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));

function ErrorState({ error, retry }: { error: unknown; retry: () => void }) {
  return <Card><div className="empty"><AlertCircle /><h3>We couldn't load this workspace</h3><p>{humanizeApiError(error, "Try again in a moment.")}</p><Button onClick={retry}>Try again</Button></div></Card>;
}
function EmptyState({ icon, title, copy }: { icon: React.ReactNode; title: string; copy: string }) {
  return <div className="empty">{icon}<h3>{title}</h3><p>{copy}</p></div>;
}
function Pager({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;
  return <div className="table-toolbar"><span className="subtle">Page {page} of {pages} · {total} records</span><div className="toolbar"><Button className="btn-sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</Button><Button className="btn-sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</Button></div></div>;
}

function customerSourceLabel(source: string) {
  const labels: Record<string, string> = {
    website_chatbot: "Website chatbot",
    whatsapp_business: "WhatsApp",
    gmail: "Gmail",
    microsoft_outlook: "Outlook",
    facebook: "Facebook",
    instagram: "Instagram",
    website: "Website",
    referral: "Referral",
    manual: "Manual",
  };

  return (
    labels[source] ??
    source
      .replaceAll("_", " ")
      .replace(/\b\w/g, (character) => character.toUpperCase())
  );
}

function isAutomatedCustomerSource(source: string) {
  return source !== "manual";
}

export function CustomersPage() {
  const { activeBusinessId, activeBusiness } = useBusiness();
  const queryClient = useQueryClient();

  const terminology = getIndustryTerminology(activeBusiness?.industry);
  const customerSingular = terminology.customerSingular;
  const customerPlural = terminology.customerPlural;
  const customerSingularLower = customerSingular.toLowerCase();
  const customerPluralLower = customerPlural.toLowerCase();

  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<Customer | null>(null);
  const [actionError, setActionError] = useState("");

  const list = useQuery({
    queryKey: [
      "operations",
      activeBusinessId,
      "customers",
      search,
      status,
      page,
    ],
    queryFn: ({ signal }) =>
      operationsApi.customers.list(
        activeBusinessId,
        {
          search,
          status: status || undefined,
          page,
          pageSize: 25,
        },
        signal,
      ),
    enabled: Boolean(activeBusinessId),
  });

  const refresh = () =>
    queryClient.invalidateQueries({
      queryKey: ["operations", activeBusinessId, "customers"],
    });

  const create = useMutation({
    mutationFn: (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);

      return operationsApi.customers.create(activeBusinessId, {
        display_name: String(form.get("display_name")),
        email: String(form.get("email")) || null,
        phone: String(form.get("phone")) || null,
        company: String(form.get("company")) || null,
        source: "manual",
        status: "active",
        tags: String(form.get("tags") ?? "")
          .split(",")
          .map((tag) => tag.trim())
          .filter(Boolean),
        notes: String(form.get("notes")) || null,
      });
    },
    onSuccess: (customer) => {
      setCreating(false);
      setSelected(customer);
      setActionError("");
      void refresh();
    },
    onError: (error) =>
      setActionError(
        humanizeApiError(
          error,
          `${customerSingular} could not be saved.`,
        ),
      ),
  });

  const update = useMutation({
    mutationFn: ({
      id,
      status: next,
    }: {
      id: string;
      status: Customer["status"];
    }) =>
      operationsApi.customers.update(activeBusinessId, id, {
        status: next,
      }),
    onSuccess: (customer) => {
      setSelected(customer);
      setActionError("");
      void refresh();
    },
    onError: (error) =>
      setActionError(
        humanizeApiError(
          error,
          `${customerSingular} could not be updated.`,
        ),
      ),
  });

  const openIntegrations = () => {
    window.location.assign("/integrations");
  };

  return (
    <>
      <PageHeader
        eyebrow={`${customerPlural} intelligence`}
        title={customerPlural}
        subtitle={`One trusted ${customerSingularLower} record across your connected channels, website assistant, and business activity.`}
        action={
          <Button onClick={() => setCreating(true)}>
            <Plus />
            Add manually
          </Button>
        }
      />

      <Card className="customer-source-card">
        <div className="customer-source-head">
          <div className="customer-source-copy">
            <div className="customer-source-icon">
              <Users />
            </div>

            <div>
              <div className="row-title">Automatic customer capture</div>
              <p className="subtle">
                Verified contacts from your website assistant and connected
                communication channels are matched or created automatically.
              </p>
            </div>
          </div>

          <Button variant="primary" onClick={openIntegrations}>
            <MessageCircle />
            Connect sources
          </Button>
        </div>

        <div className="customer-source-list" aria-label="Automatic customer sources">
          <Badge tone="success">Website chatbot</Badge>
          <Badge tone="neutral">WhatsApp</Badge>
          <Badge tone="neutral">Gmail</Badge>
          <Badge tone="neutral">Outlook</Badge>
          <Badge tone="neutral">Facebook</Badge>
          <Badge tone="neutral">Instagram</Badge>
        </div>
      </Card>

      {list.isError ? (
        <ErrorState
          error={list.error}
          retry={() => void list.refetch()}
        />
      ) : (
        <Card className="table-card customer-table-card" pad={false}>
          <div className="table-toolbar">
            <div className="search-box">
              <Search />
              <input
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setPage(1);
                }}
                placeholder="Name, email, phone, or company"
              />
            </div>

            <div className="filters">
              {["", "active", "inactive", "archived"].map((item) => (
                <button
                  key={item || "all"}
                  className={`filter ${status === item ? "active" : ""}`}
                  onClick={() => {
                    setStatus(item);
                    setPage(1);
                  }}
                >
                  {item || "All"}
                </button>
              ))}
            </div>
          </div>

          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>{customerSingular}</th>
                  <th>Contact</th>
                  <th>Company</th>
                  <th>Source</th>
                  <th>Status</th>
                  <th>Updated</th>
                  <th />
                </tr>
              </thead>

              <tbody>
                {list.data?.items.map((customer) => (
                  <tr key={customer.id}>
                    <td>
                      <div className="actor-cell">
                        <Avatar name={customer.display_name} />
                        <div>
                          <strong>{customer.display_name}</strong>
                          <div className="row-copy">
                            {isAutomatedCustomerSource(customer.source)
                              ? "Automatically captured"
                              : "Added manually"}
                          </div>
                        </div>
                      </div>
                    </td>

                    <td>
                      {customer.email || "—"}
                      <div className="row-copy">
                        {customer.phone || "No phone"}
                      </div>
                    </td>

                    <td>{customer.company || "—"}</td>

                    <td>
                      <Badge
                        tone={
                          isAutomatedCustomerSource(customer.source)
                            ? "success"
                            : "neutral"
                        }
                      >
                        {customerSourceLabel(customer.source)}
                      </Badge>
                    </td>

                    <td>
                      <Badge
                        tone={
                          customer.status === "active"
                            ? "success"
                            : customer.status === "archived"
                              ? "neutral"
                              : "warning"
                        }
                      >
                        {customer.status}
                      </Badge>
                    </td>

                    <td>{when(customer.updated_at)}</td>

                    <td>
                      <button
                        className="icon-btn"
                        onClick={() => setSelected(customer)}
                        aria-label={`Open ${customer.display_name}`}
                      >
                        <ChevronRight />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {list.isLoading && (
              <div className="empty customer-empty-state">
                <p>Loading {customerPluralLower}…</p>
              </div>
            )}

            {list.data && !list.data.items.length && (
              <div className="empty customer-empty-state">
                <Users />
                <h3>Your {customerSingularLower} database builds itself</h3>
                <p>
                  Connect a communication source or activate your website
                  assistant. Verified contacts will appear here automatically.
                  Manual entry remains available for offline records.
                </p>
              </div>
            )}
          </div>

          {list.data && (
            <Pager
              page={page}
              pageSize={list.data.page_size}
              total={list.data.total}
              onPage={setPage}
            />
          )}
        </Card>
      )}

      {creating && (
        <Modal
          title={`Add ${customerSingularLower} manually`}
          description={`Use manual entry for offline ${customerPluralLower} or records that did not arrive through a connected source.`}
          onClose={() => {
            setCreating(false);
            setActionError("");
          }}
        >
          <form onSubmit={(event) => create.mutate(event)}>
            <div className="form-grid">
              <div className="field full">
                <label>Display name</label>
                <input name="display_name" required maxLength={160} />
              </div>

              <div className="field">
                <label>Email</label>
                <input name="email" type="email" />
              </div>

              <div className="field">
                <label>Phone</label>
                <input name="phone" />
              </div>

              <div className="field">
                <label>Company</label>
                <input name="company" />
              </div>

              <div className="field">
                <label>Tags</label>
                <input name="tags" placeholder="vip, wholesale" />
              </div>

              <div className="field full">
                <label>Notes</label>
                <textarea name="notes" maxLength={4000} />
              </div>
            </div>

            {actionError && <p className="form-error">{actionError}</p>}

            <div className="modal-foot">
              <Button
                type="button"
                onClick={() => {
                  setCreating(false);
                  setActionError("");
                }}
              >
                Cancel
              </Button>

              <Button
                variant="primary"
                type="submit"
                disabled={create.isPending}
              >
                {create.isPending
                  ? "Saving…"
                  : `Add ${customerSingularLower}`}
              </Button>
            </div>
          </form>
        </Modal>
      )}

      {selected && (
        <Modal
          title={selected.display_name}
          description={`${selected.email || "No email"} · ${
            selected.phone || "No phone"
          }`}
          onClose={() => {
            setSelected(null);
            setActionError("");
          }}
        >
          <div className="grid split-grid">
            <Card>
              <div className="eyebrow">Company</div>
              <h2>
                {selected.company ||
                  `Individual ${customerSingularLower}`}
              </h2>
              <p className="subtle">
                Source · {customerSourceLabel(selected.source)}
              </p>
              <p className="subtle">
                {isAutomatedCustomerSource(selected.source)
                  ? "Captured automatically from business activity."
                  : "Created manually by your team."}
              </p>
            </Card>

            <Card>
              <div className="eyebrow">Notes</div>
              <p>{selected.notes || "No notes yet."}</p>
              {!!selected.tags.length && (
                <p className="subtle">
                  Tags · {selected.tags.join(", ")}
                </p>
              )}
            </Card>
          </div>

          <div className="field">
            <label>Status</label>
            <select
              value={selected.status}
              disabled={update.isPending}
              onChange={(event) =>
                update.mutate({
                  id: selected.id,
                  status: event.target.value as Customer["status"],
                })
              }
            >
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
              <option value="archived">Archived</option>
            </select>
          </div>

          {actionError && <p className="form-error">{actionError}</p>}
        </Modal>
      )}
    </>
  );
}

const orderTransitions: Record<OrderStatus, OrderStatus[]> = { draft: ["confirmed", "canceled"], confirmed: ["processing", "canceled"], processing: ["completed", "canceled"], completed: [], canceled: [] };
export function OrdersPage() {
  const { activeBusinessId, activeBusiness } = useBusiness(); const client = useQueryClient();
  const [search, setSearch] = useState(""); const [status, setStatus] = useState(""); const [page, setPage] = useState(1); const [creating, setCreating] = useState(false); const [selected, setSelected] = useState<Order | null>(null); const [error, setError] = useState("");
  const orders = useQuery({ queryKey: ["operations", activeBusinessId, "orders", search, status, page], queryFn: ({ signal }) => operationsApi.orders.list(activeBusinessId, { search, status: status || undefined, page, pageSize: 25 }, signal), enabled: Boolean(activeBusinessId) });
  const customers = useQuery({ queryKey: ["operations", activeBusinessId, "customers", "order-picker"], queryFn: ({ signal }) => operationsApi.customers.list(activeBusinessId, { status: "active", pageSize: 100 }, signal), enabled: Boolean(activeBusinessId) });
  const refresh = () => client.invalidateQueries({ queryKey: ["operations", activeBusinessId, "orders"] });
  const create = useMutation({ mutationFn: (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const form = new FormData(event.currentTarget); return operationsApi.orders.create(activeBusinessId, { customer_id: String(form.get("customer_id")), source: "manual", currency: activeBusiness?.currency || "USD", adjustment_amount: String(form.get("adjustment_amount") || "0"), notes: String(form.get("notes")) || null, lines: [{ description: String(form.get("description")), quantity: Number(form.get("quantity")), unit_price: String(form.get("unit_price")) }] }); }, onSuccess: (order) => { setSelected(order); setCreating(false); setError(""); void refresh(); }, onError: (reason) => setError(humanizeApiError(reason, "Order could not be created.")) });
  const move = useMutation({ mutationFn: ({ id, status: next }: { id: string; status: OrderStatus }) => operationsApi.orders.status(activeBusinessId, id, next), onSuccess: (order) => { setSelected(order); setError(""); void refresh(); }, onError: (reason) => setError(humanizeApiError(reason, "Order status could not be changed.")) });
  return <>
    <PageHeader eyebrow="Customer operations" title="Orders management" subtitle="Server-calculated totals and explicit lifecycle transitions." action={<Button variant="primary" onClick={() => setCreating(true)} disabled={!customers.data?.items.length}><Plus /> Create order</Button>} />
    {orders.isError ? <ErrorState error={orders.error} retry={() => void orders.refetch()} /> : <Card className="table-card" pad={false}><div className="table-toolbar"><div className="filters">{["", "draft", "confirmed", "processing", "completed", "canceled"].map((item) => <button key={item || "all"} className={`filter ${status === item ? "active" : ""}`} onClick={() => { setStatus(item); setPage(1); }}>{item || "All"}</button>)}</div><div className="search-box"><Search /><input value={search} onChange={(event) => { setSearch(event.target.value); setPage(1); }} placeholder="Order or customer" /></div></div><div className="table-scroll"><table><thead><tr><th>Order</th><th>Customer</th><th>Items</th><th>Total</th><th>Status</th><th>Source</th><th>Created</th><th /></tr></thead><tbody>{orders.data?.items.map((order) => <tr key={order.id}><td><strong>{order.order_number}</strong></td><td>{order.customer_display_name}</td><td>{order.lines.map((line) => `${line.description} × ${line.quantity}`).join(", ")}</td><td><strong>{money(order.total, order.currency)}</strong></td><td><Badge tone={order.status === "completed" ? "success" : order.status === "canceled" ? "neutral" : "info"}>{order.status}</Badge></td><td>{order.source}</td><td>{when(order.created_at)}</td><td><button className="icon-btn" onClick={() => setSelected(order)}><ChevronRight /></button></td></tr>)}</tbody></table>{orders.isLoading && <div className="empty"><p>Loading orders…</p></div>}{orders.data && !orders.data.items.length && <EmptyState icon={<ShoppingBag />} title="No orders in this view" copy="Create an order or change the filter." />}</div>{orders.data && <Pager page={page} pageSize={orders.data.page_size} total={orders.data.total} onPage={setPage} />}</Card>}
    {creating && <Modal title="Create order" description="Totals are calculated and validated by the backend." onClose={() => setCreating(false)}><form onSubmit={(event) => create.mutate(event)}><div className="form-grid"><div className="field full"><label>Customer</label><select name="customer_id" required>{customers.data?.items.map((item) => <option value={item.id} key={item.id}>{item.display_name}</option>)}</select></div><div className="field full"><label>Line description</label><input name="description" required maxLength={300} /></div><div className="field"><label>Quantity</label><input name="quantity" type="number" min="1" defaultValue="1" required /></div><div className="field"><label>Unit price</label><input name="unit_price" type="number" min="0" step="0.01" required /></div><div className="field"><label>Adjustment</label><input name="adjustment_amount" type="number" min="0" step="0.01" defaultValue="0" /></div><div className="field full"><label>Notes</label><textarea name="notes" /></div></div>{error && <p className="form-error">{error}</p>}<div className="modal-foot"><Button type="button" onClick={() => setCreating(false)}>Cancel</Button><Button variant="primary" type="submit" disabled={create.isPending}>{create.isPending ? "Creating…" : "Create order"}</Button></div></form></Modal>}
    {selected && <Modal wide title={selected.order_number} description={`${selected.customer_display_name} · ${when(selected.created_at)}`} onClose={() => setSelected(null)}><div className="order-detail-grid"><Card><div className="eyebrow">Customer</div><h2>{selected.customer_display_name}</h2></Card><Card><div className="eyebrow">Line items</div>{selected.lines.map((line) => <div className="stat-row" key={line.id}><span>{line.description} × {line.quantity}</span><strong>{money(Number(line.unit_price) * line.quantity, selected.currency)}</strong></div>)}</Card><Card><div className="eyebrow">Financials</div><div className="stat-row"><span>Subtotal</span><strong>{money(selected.subtotal, selected.currency)}</strong></div><div className="stat-row"><span>Adjustment</span><strong>{money(selected.adjustment_amount, selected.currency)}</strong></div><div className="stat-row"><span>Total</span><strong>{money(selected.total, selected.currency)}</strong></div></Card><Card><div className="eyebrow">Status</div><Badge tone="info">{selected.status}</Badge><div className="toolbar" style={{ marginTop: 14 }}>{orderTransitions[selected.status].map((next) => <Button key={next} onClick={() => move.mutate({ id: selected.id, status: next })} disabled={move.isPending}>{next}</Button>)}</div></Card></div>{error && <p className="form-error">{error}</p>}</Modal>}
  </>;
}

export function CrmPage() {
  const { activeBusinessId, activeBusiness } = useBusiness();
  const client = useQueryClient();

  const workspaceProfile = getIndustryWorkspaceProfile(activeBusiness?.industry);
  const terminology = workspaceProfile.terminology;
  const crm = workspaceProfile.crm;
  const leadStages = crm.stages as readonly LeadStage[];
  const leadSingular =
    workspaceProfile.dashboardVariant === "healthcare"
      ? "Inquiry"
      : "Lead";
  const leadPlural =
    workspaceProfile.dashboardVariant === "healthcare"
      ? "Inquiries"
      : "Leads";
  const [page, setPage] = useState(1); const [creating, setCreating] = useState(false); const [selected, setSelected] = useState<Lead | null>(null); const [error, setError] = useState("");
  const leads = useQuery({ queryKey: ["operations", activeBusinessId, "leads", page], queryFn: ({ signal }) => operationsApi.leads.list(activeBusinessId, { page, pageSize: 50 }, signal), enabled: Boolean(activeBusinessId) });
  const refresh = () => client.invalidateQueries({ queryKey: ["operations", activeBusinessId, "leads"] });
  const create = useMutation({ mutationFn: (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const form = new FormData(event.currentTarget); return operationsApi.leads.create(activeBusinessId, { customer_id: null, owner_user_id: null, display_name: String(form.get("display_name")), company: String(form.get("company")) || null, email: String(form.get("email")) || null, phone: String(form.get("phone")) || null, stage: "new", source: String(form.get("source")), priority: String(form.get("priority")) as Lead["priority"], qualification_state: "unqualified", estimated_value: String(form.get("estimated_value")) || null, currency: activeBusiness?.currency || "USD", expected_close_date: null, next_follow_up_at: null, notes: String(form.get("notes")) || null }); }, onSuccess: (lead) => { setSelected(lead); setCreating(false); setError(""); void refresh(); }, onError: (reason) => setError(humanizeApiError(reason, `${leadSingular} could not be created.`)) });
  const stage = useMutation({ mutationFn: ({ id, next }: { id: string; next: LeadStage }) => operationsApi.leads.stage(activeBusinessId, id, next), onSuccess: (lead) => { setSelected(lead); void refresh(); }, onError: (reason) => setError(humanizeApiError(reason, `${leadSingular} stage could not be changed.`)) });
  if (leads.isError) return <ErrorState error={leads.error} retry={() => void leads.refetch()} />;
  return <>
    <PageHeader
      eyebrow={
        workspaceProfile.dashboardVariant === "healthcare"
          ? `${terminology.customerPlural} growth`
          : "Sales workspace"
      }
      title={terminology.crmLabel}
      subtitle={`A tenant-safe pipeline for ${leadPlural.toLowerCase()} from first signal to outcome.`}
      action={
        <Button variant="primary" onClick={() => setCreating(true)}>
          <Plus /> Add {leadSingular.toLowerCase()}
        </Button>
      }
    />
    {leads.isLoading ? <Card><div className="empty"><p>Loading pipeline…</p></div></Card> : <div className="pipeline">{leadStages.map((stageName) => <div className="stage" key={stageName}><div className="stage-head"><span>{crm.stageLabels[stageName] ?? stageName}</span><span>{leads.data?.items.filter((lead) => lead.stage === stageName).length ?? 0}</span></div>{leads.data?.items.filter((lead) => lead.stage === stageName).map((lead) => <button className="lead-card" onClick={() => setSelected(lead)} key={lead.id}><div className="row-title">{lead.display_name}</div><div className="lead-company">{lead.company || "Individual"}</div><div className="lead-meta"><span>{lead.estimated_value ? money(lead.estimated_value, lead.currency) : "Value unknown"}</span><Badge tone={lead.priority === "urgent" || lead.priority === "high" ? "warning" : "neutral"}>{lead.priority}</Badge></div><div className="row-copy">{lead.source} · {lead.qualification_state}</div></button>)}</div>)}</div>}
    {leads.data && !leads.data.items.length && <Card><EmptyState icon={<Users />} title={`No ${leadPlural.toLowerCase()} yet`} copy={`Add the first ${leadSingular.toLowerCase()} to your pipeline.`} /></Card>}{leads.data && <Pager page={page} pageSize={leads.data.page_size} total={leads.data.total} onPage={setPage} />}
    {creating && <Modal title={`Add ${leadSingular.toLowerCase()}`} description={`Capture verified ${leadSingular.toLowerCase()} details.`} onClose={() => setCreating(false)}><form onSubmit={(event) => create.mutate(event)}><div className="form-grid"><div className="field"><label>Name</label><input name="display_name" required /></div><div className="field"><label>Company</label><input name="company" /></div><div className="field"><label>Email</label><input name="email" type="email" /></div><div className="field"><label>Phone</label><input name="phone" /></div><div className="field"><label>Source</label><select name="source"><option value="manual">Manual</option><option value="website">Website</option><option value="referral">Referral</option><option value="whatsapp">WhatsApp</option></select></div><div className="field"><label>Priority</label><select name="priority"><option value="medium">Medium</option><option value="high">High</option><option value="low">Low</option><option value="urgent">Urgent</option></select></div><div className="field"><label>Estimated value</label><input name="estimated_value" type="number" min="0" step="0.01" /></div><div className="field full"><label>Notes</label><textarea name="notes" /></div></div>{error && <p className="form-error">{error}</p>}<div className="modal-foot"><Button type="button" onClick={() => setCreating(false)}>Cancel</Button><Button variant="primary" type="submit" disabled={create.isPending}>Add {leadSingular.toLowerCase()}</Button></div></form></Modal>}
    {selected && <Modal title={selected.display_name} description={`${selected.company || "Individual"} · ${selected.source}`} onClose={() => setSelected(null)}><div className="analysis-grid"><Card><div className="eyebrow">Stage</div><strong>{crm.stageLabels[selected.stage] ?? selected.stage}</strong></Card><Card><div className="eyebrow">Qualification</div><strong>{selected.qualification_state}</strong></Card><Card><div className="eyebrow">Priority</div><strong>{selected.priority}</strong></Card><Card><div className="eyebrow">Value</div><strong>{selected.estimated_value ? money(selected.estimated_value, selected.currency) : "Unknown"}</strong></Card></div><Card><div className="eyebrow">Notes</div><p>{selected.notes || "No notes."}</p></Card><div className="toolbar" style={{ marginTop: 18 }}><Button disabled={stage.isPending || leadStages.indexOf(selected.stage) === 0} onClick={() => {
  const index = leadStages.indexOf(selected.stage);
  const next = leadStages[index - 1];
  if (next) stage.mutate({ id: selected.id, next });
}}><ArrowLeft /> Move back</Button><Button variant="green" disabled={stage.isPending || ["won", "lost"].includes(selected.stage)} onClick={() => {
  const index = leadStages.indexOf(selected.stage);
  const next = leadStages[index + 1];
  if (next) stage.mutate({ id: selected.id, next });
}}>Move stage <ArrowRight /></Button></div>{error && <p className="form-error">{error}</p>}</Modal>}
  </>;
}
