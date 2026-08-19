import { useEffect, useState, type ButtonHTMLAttributes, type CSSProperties, type FormEvent, type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { type BusinessInput } from '@workspace/api-client-react';
import { ErrorBoundary } from '@/components/error-boundary';
import { Toaster } from '@/components/ui/toaster';
import { TooltipProvider } from '@/components/ui/tooltip';
import { BusinessProvider, useBusiness } from './business-context';
import { Link, Route, Switch as WouterSwitch, Router as WouterRouter, useLocation } from 'wouter';
import {
  Activity, AlertCircle, Archive, ArrowDownRight, ArrowRight, BarChart3,
  Bell, Bot, Boxes, Brain, Calendar, Check, CheckCircle2, ChevronDown, ChevronRight,
  CircleHelp, ClipboardCheck, CloudUpload, Database, Edit3,
  FileText, Filter, Gauge, Globe2, Headphones, Inbox, LayoutDashboard, Lightbulb,
  Link2, Mail, Menu, MessageCircle, MoreHorizontal, Package, PanelLeftClose,
  Pencil, Plus, RefreshCw, Search, Send, Settings2, ShoppingBag,
  SlidersHorizontal, Sparkles, Target, TestTube2, TrendingUp, Upload,
  Users, Wand2, X, Zap
} from 'lucide-react';

const queryClient = new QueryClient();

type Customer = { id: number; name: string; email: string; phone: string; orders: number; spent: number; segment: string; lastActive: string };
type Order = { id: string; customer: string; items: string; total: number; payment: string; status: string; date: string };
type ToastItem = { id: number; message: string };

const initialCustomers: Customer[] = [
  { id: 1, name: 'Sarah Mitchell', email: 'sarah.mitchell@email.com', phone: '(415) 555-0182', orders: 14, spent: 486, segment: 'VIP', lastActive: '2 days ago' },
  { id: 2, name: 'David Rodriguez', email: 'david.r@email.com', phone: '(510) 555-0144', orders: 7, spent: 228, segment: 'Regular', lastActive: '5 days ago' },
  { id: 3, name: 'Aisha Khan', email: 'aisha.khan@email.com', phone: '(650) 555-0199', orders: 3, spent: 96, segment: 'New', lastActive: 'Yesterday' },
  { id: 4, name: 'Michael Chen', email: 'michael.chen@email.com', phone: '(408) 555-0117', orders: 10, spent: 331, segment: 'Regular', lastActive: '12 days ago' },
  { id: 5, name: 'Emily Wilson', email: 'emily.wilson@email.com', phone: '(707) 555-0130', orders: 9, spent: 294, segment: 'At Risk', lastActive: '45 days ago' },
];
const initialOrders: Order[] = [
  { id: '#10482', customer: 'Sarah Mitchell', items: 'Fresh Eggs × 2, Raw Honey × 1', total: 24, payment: 'Paid', status: 'Delivered', date: 'Today, 10:42 AM' },
  { id: '#10481', customer: 'David Rodriguez', items: 'Organic Tomatoes × 5kg', total: 20, payment: 'Paid', status: 'Out for Delivery', date: 'Today, 9:18 AM' },
  { id: '#10480', customer: 'Aisha Khan', items: 'Fresh Eggs × 1, Organic Tomatoes × 2kg', total: 14, payment: 'Pending', status: 'Processing', date: 'Yesterday' },
  { id: '#10479', customer: 'Michael Chen', items: 'Raw Honey × 3, Fresh Eggs × 2', total: 48, payment: 'Paid', status: 'Confirmed', date: 'Yesterday' },
  { id: '#10478', customer: 'Emily Wilson', items: 'Organic Tomatoes × 3kg', total: 12, payment: 'Paid', status: 'Pending', date: 'Mar 18, 2025' },
];
const initialConversations = [
  { id: 1, name: 'Sarah Mitchell', channel: 'WhatsApp', message: 'Do you have eggs available today?', time: '10:42 AM', unread: true, ai: true, initials: 'SM', color: 'green' },
  { id: 2, name: 'David Rodriguez', channel: 'Instagram', message: 'I want 5kg tomatoes for Friday.', time: '9:18 AM', unread: true, ai: true, initials: 'DR', color: 'orange' },
  { id: 3, name: 'Aisha Khan', channel: 'Email', message: 'Can you deliver tomorrow?', time: 'Yesterday', unread: false, ai: false, initials: 'AK', color: 'blue' },
  { id: 4, name: 'Michael Chen', channel: 'Website', message: 'What is your wholesale price?', time: 'Yesterday', unread: false, ai: true, initials: 'MC', color: 'brown' },
];
const initialLeads = [
  { id: 1, name: 'Jordan Blake', company: 'Oak & Field Cafe', source: 'Website', score: 92, value: '$2,400', stage: 'Qualified', contact: 'Today, 9:22 AM' },
  { id: 2, name: 'Nora Patel', company: 'Harvest Table', source: 'Referral', score: 78, value: '$1,180', stage: 'Contacted', contact: 'Yesterday' },
  { id: 3, name: 'Luis Garcia', company: 'Casa Verde Market', source: 'Instagram', score: 64, value: '$780', stage: 'New', contact: 'Mar 18' },
  { id: 4, name: 'Maya Brooks', company: 'Bloom Catering', source: 'WhatsApp', score: 86, value: '$1,950', stage: 'Proposal', contact: 'Mar 16' },
  { id: 5, name: 'Theo Martin', company: 'Juniper Kitchen', source: 'Website', score: 58, value: '$460', stage: 'New', contact: 'Mar 14' },
];

function useStored<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try { const saved = localStorage.getItem(key); return saved ? JSON.parse(saved) as T : initial; } catch { return initial; }
  });
  useEffect(() => { localStorage.setItem(key, JSON.stringify(value)); }, [key, value]);
  return [value, setValue] as const;
}

function cx(...items: Array<string | false | undefined>) { return items.filter(Boolean).join(' '); }
function initials(name: string) { return name.split(' ').map((n) => n[0]).join('').slice(0, 2); }
function money(value: number) { return `$${value.toLocaleString()}`; }

const navGroups = [
  { label: 'Overview', items: [{ href: '/dashboard', label: 'Dashboard', icon: LayoutDashboard }, { href: '/command', label: 'AI Command Center', icon: Sparkles }] },
  { label: 'Operations', items: [{ href: '/conversations', label: 'Conversations', icon: Inbox }, { href: '/orders', label: 'Orders', icon: ShoppingBag }, { href: '/customers', label: 'Customers', icon: Users }, { href: '/crm', label: 'Leads & CRM', icon: Target }] },
  { label: 'AI Team', items: [{ href: '/cmo', label: 'AI CMO', icon: Wand2 }, { href: '/agents', label: 'AI Agents', icon: Bot }, { href: '/automations', label: 'Automations', icon: Zap }, { href: '/approvals', label: 'Approvals', icon: ClipboardCheck }, { href: '/opportunities', label: 'Opportunities', icon: Lightbulb }] },
  { label: 'Intelligence', items: [{ href: '/analytics', label: 'Analytics', icon: BarChart3 }, { href: '/integrations', label: 'Integrations', icon: Link2 }, { href: '/brain', label: 'Business Brain', icon: Brain }] },
];

function Button({ children, variant = 'secondary', className, ...props }: { children: ReactNode; variant?: string; className?: string } & ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button className={cx('btn', `btn-${variant}`, className)} {...props}>{children}</button>;
}
function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: string }) { return <span className={`status ${tone}`}>{children}</span>; }
function PageHeader({ eyebrow, title, subtitle, action }: { eyebrow?: string; title: string; subtitle?: string; action?: ReactNode }) {
  return <div className="page-heading"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h1>{title}</h1>{subtitle && <p className="subtle">{subtitle}</p>}</div>{action && <div className="toolbar">{action}</div>}</div>;
}
function Card({ children, className = '', pad = true, style }: { children: ReactNode; className?: string; pad?: boolean; style?: CSSProperties }) { return <div className={cx('card', pad && 'card-pad', className)} style={style}>{children}</div>; }
function SectionTitle({ title, action }: { title: string; action?: ReactNode }) { return <div className="section-title"><h2>{title}</h2>{action}</div>; }
function Avatar({ name, color = '' }: { name: string; color?: string }) { return <span className={cx('avatar', color)}>{initials(name)}</span>; }
function Modal({ title, description, children, onClose }: { title: string; description?: string; children: ReactNode; onClose: () => void }) {
  return <div className="modal-backdrop" role="presentation" onMouseDown={onClose}><div className="modal" role="dialog" aria-modal="true" onMouseDown={(e) => e.stopPropagation()}><div className="modal-head"><div><h2>{title}</h2>{description && <p>{description}</p>}</div><button className="close-btn" aria-label="Close dialog" data-testid="button-close-dialog" onClick={onClose}><X /></button></div><div className="modal-body">{children}</div></div></div>;
}
function Toasts({ items, remove }: { items: ToastItem[]; remove: (id: number) => void }) {
  return <div className="toast-stack">{items.map((item) => <div className="toast" key={item.id} data-testid={`status-toast-${item.id}`}><CheckCircle2 /><span>{item.message}</span><button className="toast-close" onClick={() => remove(item.id)} aria-label="Dismiss notification"><X /></button></div>)}</div>;
}

function Shell({ children }: { children: ReactNode }) {
  const [location, setLocation] = useLocation();
  const [open, setOpen] = useState(false);
  const [command, setCommand] = useState('');
  const [businessOpen, setBusinessOpen] = useState(false);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const { businesses, activeBusiness, activeBusinessId, selectBusiness, isLoading } = useBusiness();
  const themeClass = activeBusiness?.theme === 'navy' ? 'theme-navy' : 'theme-green';
  const industryNav = activeBusiness?.industry === 'Real Estate'
    ? { href: '/properties', label: 'Properties / Listings', icon: Boxes }
    : activeBusiness?.industry === 'E-commerce'
      ? { href: '/products', label: 'Products', icon: Package }
      : activeBusiness?.industry === 'Farm/Agriculture'
        ? { href: '/inventory', label: 'Inventory / Harvest', icon: Archive }
        : null;
  const IndustryIcon = industryNav?.icon;
  const notify = (message: string) => {
    const id = Date.now(); setToasts((current) => [...current, { id, message }]);
    window.setTimeout(() => setToasts((current) => current.filter((item) => item.id !== id)), 3200);
  };
  const runCommand = (event: FormEvent) => { event.preventDefault(); if (!command.trim()) return; setCommand(''); setLocation(`/command?q=${encodeURIComponent(command.trim())}`); };
  const isActive = (href: string) => location === href || (href === '/command' && location === '/command-center') || (href === '/cmo' && location === '/marketing');
  useEffect(() => {
    if (!isLoading && !activeBusiness && location !== '/onboarding') setLocation('/onboarding');
  }, [activeBusiness, isLoading, location, setLocation]);
  if (!activeBusiness && isLoading) {
    return <div className="empty full-screen-loading"><RefreshCw /><h3>Opening your business workspace</h3><p>Loading your AI Business OS…</p></div>;
  }
  if (!activeBusiness) return <>{children}</>;
  return <div className={cx('app-shell', themeClass)} style={{ '--business-primary': activeBusiness.theme === 'navy' ? '#1E3A8A' : '#15803D' } as CSSProperties}>
    <aside className={cx('sidebar', open && 'open')}>
      <div className="brand"><div className="brand-mark">{initials(activeBusiness.name)}</div><div><div className="brand-copy">AI Business OS</div><div className="brand-sub">quietly moving business forward</div></div></div>
      <div className="nav-list">
        {navGroups.map((group) => <div key={group.label}><div className="nav-section">{group.label}</div>{group.items.map((item) => { const Icon = item.icon; return <Link key={item.href} href={item.href} className={cx('nav-item', isActive(item.href) && 'active')} data-testid={`link-nav-${item.href.slice(1)}`} onClick={() => setOpen(false)}><Icon /><span>{item.label}</span></Link>; })}{group.label === 'Operations' && industryNav && IndustryIcon && <Link href={industryNav.href} className={cx('nav-item', isActive(industryNav.href) && 'active')} data-testid={`link-nav-${industryNav.href.slice(1)}`} onClick={() => setOpen(false)}><IndustryIcon /><span>{industryNav.label}</span></Link>}</div>)}
      </div>
      <div className="sidebar-bottom">
        <Link href="/settings" className={cx('nav-item', location === '/settings' && 'active')} data-testid="link-nav-settings"><Settings2 /><span>Settings</span></Link>
        <div className="profile"><Avatar name="Alexandra Andria" /><div><div className="profile-name">Alexandra Andria</div><div className="profile-role">Owner · {activeBusiness.name}</div></div><ChevronRight size={14} color="#aaa49c" /></div>
      </div>
    </aside>
    <main className="workspace">
      <header className="topbar">
        <button className="mobile-menu" onClick={() => setOpen((v) => !v)} aria-label="Toggle navigation" data-testid="button-toggle-navigation">{open ? <PanelLeftClose /> : <Menu />}</button>
        <form className="global-search" onSubmit={runCommand}><Search /><input value={command} onChange={(e) => setCommand(e.target.value)} placeholder="Ask your AI Business Manager..." aria-label="Ask your AI Business Manager" data-testid="input-global-command" /></form>
        <div className="business-select-wrap" style={{ position: 'relative' }}>
          <button className="business-select" onClick={() => setBusinessOpen((v) => !v)} data-testid="button-business-selector"><Globe2 />{activeBusiness.name}<ChevronDown size={13} /></button>
          {businessOpen && <div className="card" style={{ position: 'absolute', top: 42, right: 0, width: 245, zIndex: 30, padding: 7 }}>{businesses.map((item) => <button key={item.id} className={cx('channel', item.id === activeBusinessId && 'active')} onClick={() => { selectBusiness(item.id); setBusinessOpen(false); notify(`Switched to ${item.name}`); }}>{item.name}{item.id === activeBusinessId && <Check size={13} />}</button>)}<button className="channel" onClick={() => { setBusinessOpen(false); setLocation('/onboarding'); }}><Plus size={13} /> Add business</button></div>}
        </div>
        <div className="top-actions"><button className="icon-btn help-btn" aria-label="Help" data-testid="button-help"><CircleHelp /></button><button className="icon-btn" aria-label="Notifications" data-testid="button-notifications"><Bell /><i className="notif-dot" /></button><Avatar name="Alexandra Andria" /><Button variant="primary" onClick={() => setLocation('/automations')} data-testid="button-new-automation"><Plus /> New automation</Button></div>
      </header>
      <div className="page">{children}</div>
    </main>
    <Toasts items={toasts} remove={(id) => setToasts((items) => items.filter((item) => item.id !== id))} />
  </div>;
}

const onboardingIndustries = ['Farm/Agriculture', 'Real Estate', 'E-commerce', 'Dental', 'Other'] as const;
const onboardingChannels = [
  { name: 'WhatsApp', description: 'Customer conversations and order updates', icon: MessageCircle },
  { name: 'Instagram', description: 'Content publishing and engagement', icon: Globe2 },
  { name: 'Email', description: 'Inbox and customer updates', icon: Mail },
  { name: 'Stripe', description: 'Payments and transaction status', icon: Archive },
] as const;
const aiTeamNames = ['Business Manager', 'CMO', 'Sales', 'Support', 'Operations'];

type OnboardingProduct = { id: string; name: string; price: string; availability: string };

function Onboarding() {
  const [, setLocation] = useLocation();
  const { createBusiness } = useBusiness();
  const [step, setStep] = useState(0);
  const [progress, setProgress] = useState(0);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');
  const [form, setForm] = useState({
    name: '',
    industry: 'Farm/Agriculture' as BusinessInput['industry'],
    website: '',
    location: '',
    timezone: 'Asia/Karachi',
    currency: 'USD · $',
    description: '',
    tone: 'Warm, grounded, and useful',
    avoidKeywords: '',
    channels: [] as NonNullable<BusinessInput['connectedChannels']>,
    products: [{ id: 'product-1', name: '', price: '', availability: 'In stock' }] as OnboardingProduct[],
  });

  useEffect(() => {
    if (step !== 4) return;
    setProgress(0);
    const timer = window.setInterval(() => {
      setProgress((value) => Math.min(100, value + 20));
    }, 430);
    return () => window.clearInterval(timer);
  }, [step]);

  const update = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const updateProduct = (id: string, field: keyof OnboardingProduct, value: string) => {
    update('products', form.products.map((product) => product.id === id ? { ...product, [field]: value } : product));
  };

  const addProduct = () => {
    update('products', [...form.products, { id: `product-${Date.now()}`, name: '', price: '', availability: 'In stock' }]);
  };

  const toggleChannel = (channel: (typeof onboardingChannels)[number]['name']) => {
    const channels = form.channels.includes(channel)
      ? form.channels.filter((item) => item !== channel)
      : [...form.channels, channel];
    update('channels', channels);
  };

  const canContinue = step === 0
    ? Boolean(form.name.trim() && form.industry)
    : step === 1
      ? form.products.some((product) => product.name.trim())
      : true;

  const next = () => {
    if (!canContinue) {
      setNotice(step === 0 ? 'Add a business name and choose an industry to continue.' : 'Add at least one product or service to continue.');
      return;
    }
    setNotice('');
    setStep((current) => Math.min(4, current + 1));
  };

  const finish = async () => {
    if (saving) return;
    setSaving(true);
    try {
      await createBusiness({
        name: form.name.trim(),
        industry: form.industry,
        website: form.website.trim(),
        location: form.location.trim(),
        timezone: form.timezone,
        currency: form.currency,
        description: form.description.trim(),
        tone: form.tone.trim(),
        avoidKeywords: form.avoidKeywords.trim(),
        connectedChannels: form.channels,
        products: form.products
          .filter((product) => product.name.trim())
          .map((product) => ({ id: product.id, name: product.name.trim(), price: Number(product.price) || 0, availability: product.availability })),
        onboardingComplete: true,
        theme: form.industry === 'Real Estate' ? 'navy' : 'green',
      });
      setLocation('/dashboard');
    } catch {
      setNotice('We could not save this workspace yet. Please try again.');
      setSaving(false);
    }
  };

  const stepTitles = ['Business basics', 'Products & services', 'Brand voice', 'Connect channels', 'AI team setup'];
  return <div className="onboarding-screen">
    <div className="onboarding-top">
      <div className="brand onboarding-brand"><div className="brand-mark">AI</div><div><div className="brand-copy">AI Business OS</div><div className="brand-sub">build your business command room</div></div></div>
      <div className="onboarding-progress">
        {stepTitles.map((title, index) => <div className={cx('onboarding-step', index === step && 'active', index < step && 'complete')} key={title}><span>{index < step ? <Check size={13} /> : index + 1}</span><small>{title}</small></div>)}
      </div>
      <div className="onboarding-help">Step {step + 1} of 5</div>
    </div>
    <div className="onboarding-progress-line"><i style={{ width: `${((step + (step === 4 ? progress / 100 : 0)) / 4) * 100}%` }} /></div>
    <main className="onboarding-body">
      <div className="onboarding-copy">
        <div className="eyebrow">Welcome to your command room</div>
        <h1>{step === 4 && progress === 100 ? 'Your AI Business Team is ready!' : stepTitles[step]}</h1>
        <p>{step === 0 ? 'Tell us a little about your business so your AI team can make better decisions from day one.' : step === 1 ? 'Give your team the products, services, and availability it should know about.' : step === 2 ? 'Your AI team will use this voice whenever it writes, replies, or recommends.' : step === 3 ? 'Choose the channels your team should be ready to work across.' : progress === 100 ? 'Your workspace is configured and your agents have a clear starting point.' : 'We are creating a focused team around the way your business works.'}</p>
      </div>
      <Card className="onboarding-card" pad={false}>
        {step === 0 && <div className="onboarding-panel"><div className="form-grid"><div className="field full"><label>Business name</label><input autoFocus value={form.name} onChange={(e) => update('name', e.target.value)} placeholder="e.g. Green Valley Farms" data-testid="input-onboarding-business-name" /></div><div className="field"><label>Industry</label><select value={form.industry} onChange={(e) => update('industry', e.target.value as BusinessInput['industry'])} data-testid="select-onboarding-industry">{onboardingIndustries.map((industry) => <option key={industry}>{industry}</option>)}</select></div><div className="field"><label>Website</label><input value={form.website} onChange={(e) => update('website', e.target.value)} placeholder="yourbusiness.com" data-testid="input-onboarding-website" /></div><div className="field"><label>Location</label><input value={form.location} onChange={(e) => update('location', e.target.value)} placeholder="City, country" data-testid="input-onboarding-location" /></div><div className="field"><label>Timezone</label><select value={form.timezone} onChange={(e) => update('timezone', e.target.value)} data-testid="select-onboarding-timezone"><option>Asia/Karachi</option><option>America/Los_Angeles</option><option>America/Chicago</option><option>America/New_York</option><option>Europe/London</option><option>UTC</option></select></div><div className="field"><label>Currency</label><select value={form.currency} onChange={(e) => update('currency', e.target.value)} data-testid="select-onboarding-currency"><option>USD · $</option><option>PKR · ₨</option><option>EUR · €</option><option>GBP · £</option></select></div></div></div>}
        {step === 1 && <div className="onboarding-panel"><div className="onboarding-list-head"><div><div className="eyebrow">Your offer</div><h2>Products and services</h2></div><Button variant="soft" className="btn-sm" onClick={addProduct} data-testid="button-add-onboarding-product"><Plus /> Add item</Button></div><div className="onboarding-products">{form.products.map((product, index) => <div className="onboarding-product-row" key={product.id}><div className="product-number">{String(index + 1).padStart(2, '0')}</div><div className="field"><label>Name</label><input value={product.name} onChange={(e) => updateProduct(product.id, 'name', e.target.value)} placeholder="Product or service" data-testid={`input-onboarding-product-name-${index}`} /></div><div className="field"><label>Price</label><input type="number" min="0" value={product.price} onChange={(e) => updateProduct(product.id, 'price', e.target.value)} placeholder="0" data-testid={`input-onboarding-product-price-${index}`} /></div><div className="field"><label>Stock / availability</label><input value={product.availability} onChange={(e) => updateProduct(product.id, 'availability', e.target.value)} placeholder="In stock" data-testid={`input-onboarding-product-availability-${index}`} /></div>{form.products.length > 1 && <button className="icon-btn" onClick={() => update('products', form.products.filter((item) => item.id !== product.id))} aria-label={`Remove product ${index + 1}`}><X size={14} /></button>}</div>)}</div><div className="onboarding-tip"><Lightbulb size={16} /><span>You can update this list any time from Settings. Your AI team uses availability when answering customer questions.</span></div></div>}
        {step === 2 && <div className="onboarding-panel"><div className="field"><label>Business description</label><textarea autoFocus value={form.description} onChange={(e) => update('description', e.target.value)} placeholder="What does your business do, and who does it serve?" data-testid="textarea-onboarding-description" /></div><div className="form-grid onboarding-form-gap"><div className="field"><label>Tone of voice</label><input value={form.tone} onChange={(e) => update('tone', e.target.value)} placeholder="Warm, clear, confident..." data-testid="input-onboarding-tone" /></div><div className="field"><label>Keywords to avoid</label><input value={form.avoidKeywords} onChange={(e) => update('avoidKeywords', e.target.value)} placeholder="Words or phrases, comma separated" data-testid="input-onboarding-avoid-keywords" /></div></div><div className="voice-preview"><div className="voice-preview-mark"><Bot size={17} /></div><div><div className="eyebrow">Voice preview</div><div className="row-title">Your AI team will sound like a thoughtful partner, not a generic chatbot.</div></div></div></div>}
        {step === 3 && <div className="onboarding-panel"><div className="channel-grid onboarding-channel-grid">{onboardingChannels.map(({ name, description, icon: Icon }) => { const connected = form.channels.includes(name); return <button key={name} className={cx('onboarding-channel-card', connected && 'connected')} onClick={() => toggleChannel(name)} data-testid={`button-onboarding-channel-${name.toLowerCase()}`}><div className="integration-icon"><Icon /></div><div className="row-main"><div className="row-title">{name}</div><div className="row-copy">{description}</div></div><span className={cx('channel-toggle', connected && 'on')}><i />{connected ? 'Connected' : 'Connect'}</span></button>; })}</div><div className="onboarding-tip"><Link2 size={16} /><span>Connections are safe to change later. For this setup, each card is ready as soon as you choose it.</span></div></div>}
        {step === 4 && <div className="onboarding-panel onboarding-team-panel">{progress < 100 ? <><div className="team-progress-head"><span>Building your AI team</span><strong>{progress}%</strong></div><div className="team-progress"><i style={{ width: `${progress}%` }} /></div><div className="ai-team-list">{aiTeamNames.map((name, index) => { const ready = progress >= (index + 1) * 20; return <div className={cx('ai-team-row', ready && 'ready')} key={name}><div className="agent-icon">{ready ? <Check /> : <RefreshCw className="spin" />}</div><div className="row-main"><div className="row-title">AI {name}</div><div className="row-copy">{ready ? 'Configured and ready' : 'Setting up your workspace context'}</div></div>{ready && <Badge tone="success">Ready</Badge>}</div>; })}</div></> : <div className="onboarding-success"><div className="success-mark"><Check /></div><div className="eyebrow">Setup complete</div><h2>Your AI Business Team is ready!</h2><p>Five focused agents are ready to help you move {form.name || 'your business'} forward.</p></div>}</div>}
        {notice && <div className="onboarding-notice"><AlertCircle size={15} />{notice}</div>}
        <div className="onboarding-actions">{step > 0 && step < 4 && <Button variant="secondary" onClick={() => { setNotice(''); setStep((current) => current - 1); }} data-testid="button-onboarding-back">Back</Button>}{step === 4 && progress === 100 && <Button variant="primary" onClick={finish} disabled={saving} data-testid="button-finish-onboarding">{saving ? 'Opening workspace…' : 'Open my dashboard'} <ArrowRight /></Button>}{step < 4 && <Button variant="green" onClick={next} data-testid="button-onboarding-next">{step === 3 ? 'Build my AI team' : 'Continue'} <ArrowRight /></Button>}</div>
      </Card>
    </main>
  </div>;
}

function Dashboard() {
  const [, setLocation] = useLocation();
  const { activeBusiness } = useBusiness();
  const businessName = activeBusiness?.name ?? 'your business';
  const industry = activeBusiness?.industry ?? 'Other';
  const isRealEstate = industry === 'Real Estate';
  const primaryProduct = activeBusiness?.products[0]?.name ?? (isRealEstate ? 'New listings' : 'Your top offer');
  const dashboardOrders: Order[] = isRealEstate
    ? [
      { id: '#RE-218', customer: 'Maya Brooks', items: 'Oak Hills Home · Viewing request', total: 725000, payment: 'Paid', status: 'Confirmed', date: 'Today, 10:42 AM' },
      { id: '#RE-217', customer: 'Jordan Blake', items: 'Downtown Loft · Offer review', total: 485000, payment: 'Pending', status: 'Processing', date: 'Today, 9:18 AM' },
      { id: '#RE-216', customer: 'Nora Patel', items: 'River Road Lot · Listing inquiry', total: 210000, payment: 'Paid', status: 'Pending', date: 'Yesterday' },
    ]
    : initialOrders;
  const recommendationTitle = isRealEstate
    ? `${primaryProduct} is getting high-intent attention.`
    : industry === 'Farm/Agriculture'
      ? `${primaryProduct} are trending this week. Create a post.`
      : `${primaryProduct} is your strongest current offer.`;
  const recommendationCopy = isRealEstate
    ? 'Respond quickly to the newest viewing request while the buyer signal is warm.'
    : 'Turn this momentum into a useful, timely message for your audience.';
  const [approvals, setApprovals] = useStored('ai-os-approvals', [
    { id: 1, agent: 'AI Sales', title: 'Send follow-up to Sarah Mitchell', detail: 'High-LTV customer has not ordered for 45 days.', status: 'Pending' },
    { id: 2, agent: 'AI CMO', title: 'Publish Instagram harvest post', detail: 'Engagement on harvest content is trending up.', status: 'Pending' },
    { id: 3, agent: 'AI Sales', title: 'Approve 10% reactivation offer', detail: '5 inactive customers represent $1,240 potential revenue.', status: 'Pending' },
  ]);
  const [notice, setNotice] = useState('');
  const act = (id: number, status: string) => { setApprovals((items) => items.map((item) => item.id === id ? { ...item, status } : item)); setNotice(status === 'Approved' ? 'Approval completed and action queued.' : 'Approval rejected. The AI team has been notified.'); };
  return <><PageHeader eyebrow="Tuesday · March 19, 2025" title={`Good morning, ${businessName}`} subtitle={`Here is what your AI Business Team found for ${businessName}.`} action={<Button variant="soft" onClick={() => setLocation('/command')} data-testid="button-open-manager"><Sparkles /> Ask your manager</Button>} />
    {notice && <div className="ai-banner"><CheckCircle2 />{notice}<button className="close-btn" onClick={() => setNotice('')}><X size={14} /></button></div>}
    <div className="grid kpi-grid">
      <Kpi title={isRealEstate ? "Pipeline value" : "Today's revenue"} value={isRealEstate ? "$1.42m" : "$4,820"} foot={isRealEstate ? "+12% from last period" : "+18% from last period"} icon={<TrendingUp />} tone="green" />
      <Kpi title={isRealEstate ? "Active listings" : "Pending orders"} value={isRealEstate ? "18" : "24"} foot={isRealEstate ? "4 need attention" : "3 need attention"} icon={<Package />} tone="orange" />
      <Kpi title="Unread messages" value="42" foot="WhatsApp · Instagram · Email" icon={<MessageCircle />} tone="brown" />
      <Kpi title="New leads" value="31" foot="8 high intent" icon={<Target />} tone="rose" />
    </div>
    <div className="grid split-grid" style={{ marginTop: 14 }}>
      <Card className="health-card"><SectionTitle title="Business health" action={<Badge tone="success">Healthy</Badge>} /><div className="health-layout"><div><div className="score-ring"><div className="score-inner"><strong>87</strong><span>out of 100</span></div></div><p style={{ textAlign: 'center', color: '#807970', fontSize: 10 }}>Stable momentum this week</p></div><div className="health-bars">{[['Sales', 92], ['Marketing', 81], ['Customers', 88], ['Support', 95], ['Operations', 84]].map(([label, value]) => <div className="health-row" key={label as string}><span>{label}</span><div className="bar"><i style={{ width: `${value}%` }} /></div><strong>{value}</strong></div>)}</div></div></Card>
       <Card className="recommendation"><SectionTitle title="AI recommendations" action={<Link href="/opportunities" className="btn btn-sm btn-soft" data-testid="link-review-opportunities">Review all <ArrowRight /></Link>} /><div className="recommendation-item"><div className="rec-icon"><RefreshCw /></div><div className="rec-title">{recommendationTitle}</div><div className="rec-copy">{recommendationCopy}</div><div className="rec-value">{isRealEstate ? 'Potential commission · $42,600' : 'Potential revenue · $1,240'}</div></div><div className="recommendation-item"><div className="rec-icon"><TrendingUp /></div><div className="rec-title">{isRealEstate ? 'Buyer inquiries increased 24%.' : 'Instagram engagement increased 32%.'}</div><div className="rec-copy">{isRealEstate ? 'Create a follow-up sequence around the listings buyers are saving.' : 'Create another Reel around the new harvest while interest is warm.'}</div><Button variant="green" className="btn-sm" onClick={() => setLocation('/cmo')} data-testid="button-create-reel">{isRealEstate ? 'Create listing campaign' : 'Create content'}</Button></div></Card>
    </div>
    <div className="grid three-grid"><Card><SectionTitle title="Approval tasks" action={<Link href="/approvals" className="subtle" data-testid="link-view-approvals">View all</Link>} /><div className="list">{approvals.filter((item) => item.status === 'Pending').map((item) => <div className="list-row" key={item.id}><div className="row-main"><div className="row-title">{item.title}</div><div className="row-copy">{item.agent} · {item.detail}</div></div><div className="toolbar"><Button variant="green" className="btn-sm" onClick={() => act(item.id, 'Approved')} data-testid={`button-approve-dashboard-${item.id}`}><Check /></Button><Button variant="danger" className="btn-sm" onClick={() => act(item.id, 'Rejected')} data-testid={`button-reject-dashboard-${item.id}`}><X /></Button></div></div>)}{approvals.every((item) => item.status !== 'Pending') && <div className="empty"><CheckCircle2 /><h3>All clear</h3><p>No actions are waiting for your approval.</p></div>}</div></Card><Card><SectionTitle title="Recent conversations" action={<Link href="/conversations" className="subtle" data-testid="link-view-conversations">View inbox</Link>} /><div className="list">{initialConversations.slice(0, 4).map((conversation) => <div className="list-row" key={conversation.id}><Avatar name={conversation.name} /><div className="row-main"><div className="row-title">{conversation.name} <span style={{ color: '#aaa49c', fontWeight: 400 }}>· {conversation.channel}</span></div><div className="row-copy">{conversation.message}</div></div><div className="time">{conversation.time}</div></div>)}</div></Card><Card><SectionTitle title="AI activity" action={<Activity size={15} color="#9a938a" />} /><div>{[['10:42 AM', 'AI Support answered Sarah’s question'], ['10:44 AM', 'AI Sales qualified a new lead'], ['10:47 AM', 'AI Operations confirmed Order #10482'], ['10:52 AM', 'AI CMO generated Instagram content']].map(([time, text]) => <div className="activity-line" key={time}><div className="activity-dot" /><div><div className="activity-time">{time}</div><div className="activity-copy">{text}</div></div></div>)}</div></Card></div>
     <SectionTitle title={isRealEstate ? 'Recent opportunities' : 'Recent orders'} action={<Link href="/orders" className="btn btn-sm btn-secondary" data-testid="link-view-orders">View orders <ArrowRight /></Link>} /><Card className="table-card" pad={false}><div className="table-scroll"><table><thead><tr><th>Order</th><th>Customer</th><th>Items</th><th>Total</th><th>Status</th><th>Time</th></tr></thead><tbody>{dashboardOrders.slice(0, 4).map((order) => <tr key={order.id}><td><strong>{order.id}</strong></td><td>{order.customer}</td><td>{order.items}</td><td><strong>{money(order.total)}</strong></td><td><Badge tone={order.status === 'Delivered' ? 'success' : order.status === 'Pending' ? 'warning' : 'info'}>{order.status}</Badge></td><td>{order.date}</td></tr>)}</tbody></table></div></Card>
  </>;
}
function Kpi({ title, value, foot, icon, tone }: { title: string; value: string; foot: string; icon: ReactNode; tone: string }) { return <Card className="kpi"><div className="kpi-top"><span>{title}</span><div className={`kpi-icon ${tone}`}>{icon}</div></div><div className="kpi-value">{value}</div><div className="kpi-foot"><span className={tone === 'rose' ? 'trend-down' : 'trend-up'}>{tone === 'rose' ? '8 high intent' : foot.split(' ')[0]}</span><span>{foot}</span></div></Card>; }

function CommandCenter() {
  const [, setLocation] = useLocation();
  const [query, setQuery] = useState('');
  const [response, setResponse] = useState<{ answer: string; data: string[]; reason: string; action: string } | null>(null);
  const examples = ['How is my business doing?', 'What should I focus on today?', 'Why did sales decrease?', 'Find inactive customers', 'Create a marketing campaign', 'Show today’s orders', 'Follow up with my high-value leads'];
  const ask = (text: string) => {
    setQuery(text);
    const lower = text.toLowerCase();
    if (lower.includes('inactive') || lower.includes('campaign')) setResponse({ answer: '12 customers have not ordered in 60+ days. Fresh Eggs is your strongest repeat product, and a reactivation offer would be timely.', data: ['Customer history · 5 at-risk profiles', 'Orders · 60-day purchase window', 'Product performance · Fresh Eggs'], reason: 'These customers have a strong prior purchase pattern but their order cadence has paused. A focused message is lower risk than a broad discount.', action: 'Create a reactivation campaign' });
    else if (lower.includes('order')) setResponse({ answer: 'There are 24 pending orders today. 3 are waiting for an owner decision, while Order #10481 is already out for delivery.', data: ['Orders · Today', 'Operations activity · 24 pending', 'Delivery status · #10481'], reason: 'The order queue is healthy, but three pending actions could slow same-day fulfilment.', action: 'Review pending orders' });
    else setResponse({ answer: 'Revenue is up 18% this week. Fresh Eggs is the strongest product, and customer support is resolving 94% of questions without escalation.', data: ['Revenue · +18% this week', 'Product mix · Fresh Eggs leading', 'Support · 94% auto-resolved'], reason: 'Sales momentum and support efficiency are both above your recent baseline. The clearest opportunity is converting new demand into repeat orders.', action: 'View business opportunities' });
  };
  return <><PageHeader eyebrow="Business Manager" title="AI Command Center" subtitle="Ask anything about your business or give your AI team a task." /><Card className="card-pad" style={{ maxWidth: 930 }}><div style={{ display: 'flex', gap: 10, alignItems: 'center', border: '1px solid #ddd8d1', padding: '6px 7px 6px 13px', borderRadius: 9, background: '#fcfbfa' }}><Sparkles size={17} color="#16803d" /><input value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') ask(query); }} placeholder="Ask about revenue, customers, orders, or give your AI team a task..." style={{ border: 0, outline: 0, flex: 1, background: 'transparent', fontSize: 12 }} data-testid="input-command-center" /><Button variant="green" onClick={() => ask(query)} data-testid="button-send-command"><Send /></Button></div><div className="eyebrow" style={{ marginTop: 20, marginBottom: 10 }}>Try asking</div><div className="filters">{examples.map((example) => <button key={example} className="filter active" onClick={() => ask(example)} data-testid={`button-example-${example.slice(0, 8).replaceAll(' ', '-')}`}>{example}</button>)}</div></Card>{response ? <Card className="card-pad" style={{ maxWidth: 930, marginTop: 15 }}><div className="ai-banner"><Bot />Manager response · grounded in your business data</div><h2 style={{ fontSize: 18, maxWidth: 720, lineHeight: 1.45 }}>{response.answer}</h2><div className="grid split-grid" style={{ marginTop: 20 }}><div><div className="eyebrow">Data used</div>{response.data.map((item) => <div className="list-row" key={item}><Database size={14} color="#4b9e61" /><div className="row-title">{item}</div><Check size={14} color="#4b9e61" /></div>)}</div><div><div className="eyebrow">Why this matters</div><p className="subtle" style={{ lineHeight: 1.7 }}>{response.reason}</p><div className="eyebrow" style={{ marginTop: 18 }}>Recommended action</div><div className="card" style={{ padding: 12, background: '#fff9f1', borderColor: '#f6e1c9' }}><div className="row-title">{response.action}</div><p className="row-copy">This action will be staged for your review before anything is sent.</p><div className="toolbar" style={{ marginTop: 10 }}><Button variant="primary" className="btn-sm" onClick={() => setLocation(response.action.includes('campaign') ? '/marketing' : response.action.includes('orders') ? '/orders' : '/opportunities')} data-testid="button-execute-recommendation">Review action <ArrowRight /></Button><Button variant="secondary" className="btn-sm" onClick={() => setResponse(null)} data-testid="button-clear-response">Clear</Button></div></div></div></div></Card> : <div className="empty" style={{ maxWidth: 930, marginTop: 28 }}><Sparkles /><h3>Your business context is ready</h3><p>Ask a question to see what happened, why it happened, and what your AI team recommends next.</p></div>}</>;
}

function Conversations() {
  const [selected, setSelected] = useState(initialConversations[0]);
  const [filter, setFilter] = useState('All');
  const [takeover, setTakeover] = useState(false);
  const [message, setMessage] = useState('');
  const [sent, setSent] = useState<string[]>([]);
  const channels = ['All', 'WhatsApp', 'Instagram', 'Facebook', 'Email', 'Website'];
  const shown = initialConversations.filter((item) => filter === 'All' || item.channel === filter);
  const send = () => { if (!message.trim()) return; setSent((items) => [...items, message.trim()]); setMessage(''); };
  return <><PageHeader eyebrow="Customer operations" title="Conversations" subtitle="One calm inbox for every customer channel." action={<Button variant="green" onClick={() => setTakeover((v) => !v)} data-testid="button-toggle-takeover">{takeover ? 'Return to AI' : 'Take over inbox'}</Button>} /><div className="card conversation-layout"><div className="conversation-panel"><div className="panel-label">Channels</div>{channels.map((channel) => <button key={channel} className={cx('channel', filter === channel && 'active')} onClick={() => setFilter(channel)} data-testid={`button-channel-${channel.toLowerCase()}`}>{channel}<span className="count">{channel === 'All' ? 42 : channel === 'WhatsApp' ? 18 : channel === 'Instagram' ? 12 : channel === 'Email' ? 7 : 0}</span></button>)}</div><div className="conversation-panel"><div className="panel-label">Inbox <span style={{ float: 'right' }}>{shown.length}</span></div>{shown.map((item) => <div className={cx('convo-item', selected.id === item.id && 'active')} key={item.id} onClick={() => setSelected(item)} data-testid={`card-conversation-${item.id}`}><div className="convo-line"><Avatar name={item.name} /><div className="row-main"><div className="convo-name">{item.name} {item.ai && <Badge tone="success">AI</Badge>}</div><div className="convo-msg">{item.message}</div></div><div className="time">{item.time}</div></div></div>)}</div><div className="conversation-panel"><div className="chat-head"><div style={{ display: 'flex', gap: 10, alignItems: 'center' }}><Avatar name={selected.name} /><div><div className="row-title">{selected.name}</div><div className="row-copy">{selected.channel} · {takeover ? 'Human takeover' : 'AI handling'}</div></div></div><button className="icon-btn" data-testid="button-more-conversation"><MoreHorizontal /></button></div><div className="chat-body"><div className="ai-banner"><Bot />{takeover ? 'You are handling this conversation' : 'AI Assistant is handling this conversation'}<span style={{ marginLeft: 'auto' }}><Badge tone={takeover ? 'warning' : 'success'}>{takeover ? 'Human' : 'Active'}</Badge></span></div><div className="bubble-wrap customer"><div className="bubble">{selected.message}<span className="bubble-time">{selected.time}</span></div></div><div className="bubble-wrap"><div className="bubble">Thanks for reaching out. We have a fresh delivery arriving this morning. I can reserve what you need.<span className="bubble-time">10:43 AM · AI Support</span></div></div>{sent.map((item, index) => <div className="bubble-wrap customer" key={`${item}-${index}`}><div className="bubble">{item}<span className="bubble-time">Just now · You</span></div></div>)}<div className="section-title"><h3>Customer actions</h3></div><div className="toolbar"><Button variant="secondary" className="btn-sm" data-testid="button-search-customer"><Search /> Search customer</Button><Button variant="secondary" className="btn-sm" data-testid="button-create-order-chat"><Plus /> Create order</Button><Button variant="secondary" className="btn-sm" data-testid="button-escalate-chat"><AlertCircle /> Escalate</Button></div></div><div className="chat-compose"><button className="icon-btn" aria-label="Attach file" data-testid="button-attach-message"><Upload /></button><input value={message} onChange={(e) => setMessage(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') send(); }} placeholder="Write a reply..." data-testid="input-chat-reply" /><Button variant="green" onClick={send} data-testid="button-send-reply"><Send /></Button></div></div></div></>;
}

function Orders() {
  const [orders, setOrders] = useStored<Order[]>('ai-os-orders', initialOrders);
  const [filter, setFilter] = useState('All');
  const [selected, setSelected] = useState<Order | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [search, setSearch] = useState('');
  const shown = orders.filter((order) => (filter === 'All' || order.status === filter) && `${order.id} ${order.customer}`.toLowerCase().includes(search.toLowerCase()));
  const statusTone = (status: string) => status === 'Delivered' ? 'success' : status === 'Pending' ? 'warning' : status === 'Cancelled' ? 'danger' : 'info';
  const createOrder = (e: FormEvent<HTMLFormElement>) => { e.preventDefault(); const data = new FormData(e.currentTarget); const order: Order = { id: `#${10483 + orders.length}`, customer: String(data.get('customer')), items: String(data.get('items')), total: Number(data.get('total')), payment: 'Pending', status: 'Confirmed', date: 'Just now' }; setOrders((items) => [order, ...items]); setShowCreate(false); };
  return <><PageHeader eyebrow="Customer operations" title="Orders management" subtitle="Track every order from payment to delivery." action={<Button variant="primary" onClick={() => setShowCreate(true)} data-testid="button-create-order"><Plus /> Create order</Button>} /><Card className="table-card" pad={false}><div className="table-toolbar"><div className="filters">{['All', 'Pending', 'Confirmed', 'Processing', 'Out for Delivery', 'Delivered', 'Cancelled'].map((item) => <button className={cx('filter', filter === item && 'active')} key={item} onClick={() => setFilter(item)} data-testid={`button-order-filter-${item.toLowerCase().replaceAll(' ', '-')}`}>{item}</button>)}</div><div className="search-box"><Search /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search orders..." data-testid="input-order-search" /></div></div><div className="table-scroll"><table><thead><tr><th>Order ID</th><th>Customer</th><th>Items</th><th>Total</th><th>Payment</th><th>Status</th><th>Date</th><th /></tr></thead><tbody>{shown.map((order) => <tr key={order.id}><td><strong>{order.id}</strong></td><td>{order.customer}</td><td>{order.items}</td><td><strong>{money(order.total)}</strong></td><td><Badge tone={order.payment === 'Paid' ? 'success' : 'warning'}>{order.payment}</Badge></td><td><Badge tone={statusTone(order.status)}>{order.status}</Badge></td><td>{order.date}</td><td><button className="icon-btn" onClick={() => setSelected(order)} aria-label={`View ${order.id}`} data-testid={`button-view-order-${order.id.slice(1)}`}><ChevronRight /></button></td></tr>)}</tbody></table>{!shown.length && <div className="empty"><Package /><h3>No orders in this view</h3><p>Try another status or create your first order.</p></div>}</div></Card>{showCreate && <Modal title="Create order" description="Add a manual order to the Green Valley Farms queue." onClose={() => setShowCreate(false)}><form onSubmit={createOrder}><div className="form-grid"><div className="field"><label>Customer</label><select name="customer" defaultValue="Sarah Mitchell">{initialCustomers.map((customer) => <option key={customer.id}>{customer.name}</option>)}</select></div><div className="field"><label>Total</label><input name="total" type="number" min="1" defaultValue="24" /></div><div className="field full"><label>Items</label><input name="items" defaultValue="Fresh Eggs × 2, Raw Honey × 1" /></div></div><div className="modal-foot"><Button type="button" onClick={() => setShowCreate(false)} data-testid="button-cancel-order">Cancel</Button><Button variant="primary" type="submit" data-testid="button-save-order">Create order</Button></div></form></Modal>}{selected && <Modal title={`Order ${selected.id}`} description={`${selected.customer} · ${selected.date}`} onClose={() => setSelected(null)}><div className="grid" style={{ gap: 12 }}><div className="card card-pad"><div className="eyebrow">Products</div><div className="row-title">{selected.items}</div><div className="row-copy">Order total · {money(selected.total)}</div></div><div className="card card-pad"><div className="eyebrow">Status</div><div className="toolbar"><Badge tone={statusTone(selected.status)}>{selected.status}</Badge>{selected.status !== 'Delivered' && <Button variant="green" className="btn-sm" onClick={() => { const next = selected.status === 'Confirmed' ? 'Processing' : selected.status === 'Processing' ? 'Out for Delivery' : 'Delivered'; const updated = { ...selected, status: next }; setSelected(updated); setOrders((items) => items.map((item) => item.id === selected.id ? updated : item)); }} data-testid="button-advance-order">Move to next step <ArrowRight /></Button>}</div></div><div><div className="eyebrow">Timeline</div>{['Order created', 'Payment received', 'Processing', 'Out for delivery', 'Delivered'].map((step, index) => <div className="activity-line" key={step}><div className="activity-dot" style={{ background: index <= 2 ? '#55a36b' : '#ddd9d4' }} /><div className="activity-copy">{step}<div className="activity-time">{index <= 2 ? 'Completed' : 'Waiting'}</div></div></div>)}</div></div></Modal>}</>;
}

function Customers() {
  const [customers, setCustomers] = useStored<Customer[]>('ai-os-customers', initialCustomers);
  const [search, setSearch] = useState('');
  const [segment, setSegment] = useState('All');
  const [selected, setSelected] = useState<Customer | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const shown = customers.filter((customer) => (segment === 'All' || customer.segment === segment) && `${customer.name} ${customer.email} ${customer.phone}`.toLowerCase().includes(search.toLowerCase()));
  const createCustomer = (e: FormEvent<HTMLFormElement>) => { e.preventDefault(); const data = new FormData(e.currentTarget); const customer: Customer = { id: Date.now(), name: String(data.get('name')), email: String(data.get('email')), phone: String(data.get('phone')), orders: 0, spent: 0, segment: 'New', lastActive: 'Just now' }; setCustomers((items) => [customer, ...items]); setShowCreate(false); };
  return <><PageHeader eyebrow="Customer operations" title="Customer database" subtitle="Know the people behind every order." action={<Button variant="primary" onClick={() => setShowCreate(true)} data-testid="button-add-customer"><Plus /> Add customer</Button>} /><Card className="table-card" pad={false}><div className="table-toolbar"><div className="toolbar"><div className="search-box"><Search /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Name, email, or phone" data-testid="input-customer-search" /></div><Filter size={15} color="#9a938a" /></div><div className="filters">{['All', 'VIP', 'Regular', 'New', 'At Risk', 'Inactive'].map((item) => <button className={cx('filter', segment === item && 'active')} key={item} onClick={() => setSegment(item)} data-testid={`button-segment-${item.toLowerCase().replace(' ', '-')}`}>{item}</button>)}</div></div><div className="table-scroll"><table><thead><tr><th>Customer</th><th>Contact</th><th>Orders</th><th>Total spent</th><th>Segment</th><th>Last active</th><th /></tr></thead><tbody>{shown.map((customer) => <tr key={customer.id}><td><div style={{ display: 'flex', alignItems: 'center', gap: 9 }}><Avatar name={customer.name} /><strong>{customer.name}</strong></div></td><td><div>{customer.email}</div><div className="row-copy">{customer.phone}</div></td><td>{customer.orders}</td><td><strong>{money(customer.spent)}</strong></td><td><Badge tone={customer.segment === 'VIP' ? 'success' : customer.segment === 'At Risk' ? 'warning' : 'neutral'}>{customer.segment}</Badge></td><td>{customer.lastActive}</td><td><button className="icon-btn" onClick={() => setSelected(customer)} data-testid={`button-view-customer-${customer.id}`}><ChevronRight /></button></td></tr>)}</tbody></table>{!shown.length && <div className="empty"><Users /><h3>No customers found</h3><p>Try another search or segment.</p></div>}</div></Card>{showCreate && <Modal title="Add customer" description="Create a customer profile for your team." onClose={() => setShowCreate(false)}><form onSubmit={createCustomer}><div className="form-grid"><div className="field full"><label>Full name</label><input name="name" required placeholder="Customer name" data-testid="input-new-customer-name" /></div><div className="field"><label>Email</label><input name="email" type="email" required placeholder="name@email.com" /></div><div className="field"><label>Phone</label><input name="phone" required placeholder="(555) 555-0000" /></div></div><div className="modal-foot"><Button type="button" onClick={() => setShowCreate(false)}>Cancel</Button><Button variant="primary" type="submit" data-testid="button-save-customer">Add customer</Button></div></form></Modal>}{selected && <CustomerProfile customer={selected} onClose={() => setSelected(null)} onNotify={() => setSelected(null)} />}</>;
}
function CustomerProfile({ customer, onClose, onNotify }: { customer: Customer; onClose: () => void; onNotify: () => void }) {
  return <Modal title={customer.name} description={`${customer.email} · ${customer.phone}`} onClose={onClose}><div className="tabs"><button className="tab active">Overview</button><button className="tab">Orders</button><button className="tab">Activity</button></div><div className="grid kpi-grid"><Kpi title="Total orders" value={`${customer.orders}`} foot="Lifetime" icon={<ShoppingBag />} tone="green" /><Kpi title="Total spent" value={money(customer.spent)} foot="Lifetime value" icon={<TrendingUp />} tone="orange" /></div><div className="card card-pad" style={{ marginTop: 14, background: '#f0faf2' }}><div className="eyebrow">AI insight</div><div className="row-title">{customer.segment === 'VIP' ? 'High-value customer with a dependable order cadence.' : customer.segment === 'At Risk' ? 'Has not ordered for 45 days. A gentle reactivation message is recommended.' : 'Building a healthy relationship with Green Valley Farms.'}</div><p className="row-copy" style={{ whiteSpace: 'normal', marginTop: 8 }}>AI has reviewed purchase history, support interactions, and recent activity.</p></div><div className="toolbar" style={{ marginTop: 17 }}><Button variant="primary" onClick={() => { onNotify(); }} data-testid="button-send-follow-up"><Send /> Send follow-up</Button><Button variant="secondary" onClick={() => { onNotify(); }} data-testid="button-create-offer"><Sparkles /> Create offer</Button></div></Modal>;
}

function CRM() {
  const [leads, setLeads] = useStored('ai-os-leads', initialLeads);
  const [selected, setSelected] = useState<typeof initialLeads[number] | null>(null);
  const stages = ['New', 'Qualified', 'Contacted', 'Proposal', 'Won', 'Lost'];
  const move = (lead: typeof initialLeads[number], direction: number) => { const index = stages.indexOf(lead.stage); const next = stages[Math.max(0, Math.min(stages.length - 1, index + direction))]; setLeads((items) => items.map((item) => item.id === lead.id ? { ...item, stage: next } : item)); setSelected({ ...lead, stage: next }); };
  return <><PageHeader eyebrow="Sales workspace" title="Leads & CRM" subtitle="A clear path from first signal to signed relationship." action={<Button variant="primary" onClick={() => setSelected({ id: 99, name: 'New lead', company: 'Add a prospect', source: 'Manual', score: 50, value: '$0', stage: 'New', contact: 'Just now' })} data-testid="button-add-lead"><Plus /> Add lead</Button>} /><div className="pipeline">{stages.map((stage) => <div className="stage" key={stage}><div className="stage-head"><span>{stage}</span><span>{leads.filter((lead) => lead.stage === stage).length}</span></div>{leads.filter((lead) => lead.stage === stage).map((lead) => <div className="lead-card" key={lead.id} onClick={() => setSelected(lead)} data-testid={`card-lead-${lead.id}`}><div className="row-title">{lead.name}</div><div className="lead-company">{lead.company}</div><div className="lead-meta"><span>{lead.value}</span><span className="score">{lead.score}</span></div><div className="row-copy" style={{ marginTop: 7 }}>{lead.source} · {lead.contact}</div></div>)}</div>)}</div>{selected && <Modal title={selected.name} description={`${selected.company} · ${selected.source}`} onClose={() => setSelected(null)}><div className="card card-pad" style={{ background: '#f0faf2' }}><div className="eyebrow">AI qualification</div><div style={{ display: 'flex', alignItems: 'center', gap: 12 }}><div className="score" style={{ fontSize: 22, padding: '7px 10px' }}>{selected.score}/100</div><div><div className="row-title">{selected.score > 80 ? 'High value' : 'Promising lead'}</div><div className="row-copy">AI confidence based on intent, budget, and conversation context.</div></div></div></div><div className="stat-row"><span>Pipeline value</span><strong>{selected.value}</strong></div><div className="stat-row"><span>Recommended next step</span><strong>{selected.score > 80 ? 'Contact within 1 hour' : 'Send a useful follow-up'}</strong></div><div className="toolbar" style={{ marginTop: 18 }}><Button variant="secondary" className="btn-sm" onClick={() => move(selected, -1)} disabled={selected.stage === 'New'} data-testid="button-move-lead-back"><ArrowDownRight /> Move back</Button><Button variant="green" className="btn-sm" onClick={() => move(selected, 1)} disabled={selected.stage === 'Lost' || selected.stage === 'Won'} data-testid="button-move-lead-forward">Move stage <ArrowRight /></Button></div></Modal>}</>;
}

function Marketing() {
  const [showGenerator, setShowGenerator] = useState(false);
  const [generated, setGenerated] = useState(false);
  const [approved, setApproved] = useState(false);
  const [calendar, setCalendar] = useStored('ai-os-calendar', [
    ['Monday', 'Instagram Reel', 'New harvest: from our fields to your table', 'Scheduled'],
    ['Tuesday', 'Facebook Post', 'Three simple ways to use raw honey', 'Draft'],
    ['Wednesday', 'LinkedIn Post', 'The people behind Green Valley Farms', 'Scheduled'],
    ['Thursday', 'Instagram Carousel', 'Meet the spring harvest', 'Needs approval'],
    ['Friday', 'Instagram Reel', 'Fresh Eggs, simply better', 'Draft'],
  ]);
  const generate = (e: FormEvent<HTMLFormElement>) => { e.preventDefault(); setShowGenerator(false); setGenerated(true); };
  return <><PageHeader eyebrow="AI CMO" title="AI Marketing Manager" subtitle="A steady stream of useful content, ready for your review." action={<Button variant="primary" onClick={() => setShowGenerator(true)} data-testid="button-generate-content"><Wand2 /> Generate content</Button>} /><div className="grid kpi-grid"><Kpi title="Reach this month" value="18.4k" foot="+21% from last month" icon={<Globe2 />} tone="green" /><Kpi title="Engagement" value="6.8%" foot="+32% from last month" icon={<TrendingUp />} tone="orange" /><Kpi title="New leads" value="31" foot="+8 from campaigns" icon={<Target />} tone="brown" /><Kpi title="Conversion" value="4.2%" foot="+0.8% from last month" icon={<BarChart3 />} tone="rose" /></div><div className="grid split-grid"><Card><SectionTitle title="Content preview" action={generated ? <Badge tone={approved ? 'success' : 'warning'}>{approved ? 'Approved' : 'Needs review'}</Badge> : <Badge>Ready when you are</Badge>} />{generated ? <><div className="card" style={{ padding: 18, background: '#f9f6ef', borderColor: '#eee3d1' }}><div className="eyebrow">Instagram Reel · Spring harvest</div><h2 style={{ lineHeight: 1.5 }}>Fresh mornings start here. Our new harvest is picked with care, packed within hours, and headed to your table.</h2><p className="subtle" style={{ marginTop: 12 }}>#GreenValleyFarms #FromTheFarm</p></div><div className="toolbar" style={{ marginTop: 13 }}><Button variant="secondary" className="btn-sm" onClick={() => setGenerated(false)} data-testid="button-regenerate-content"><RefreshCw /> Regenerate</Button><Button variant="soft" className="btn-sm" onClick={() => setApproved(true)} data-testid="button-approve-content"><Check /> Approve</Button><Button variant="primary" className="btn-sm" onClick={() => { setCalendar((items) => items.map((item, i) => i === 0 ? [item[0], item[1], item[2], 'Scheduled'] : item)); setApproved(true); }} data-testid="button-schedule-content"><Calendar /> Schedule</Button></div></> : <div className="empty"><Wand2 /><h3>No draft open</h3><p>Give the CMO a prompt and it will prepare a post in your brand voice.</p><Button variant="soft" className="btn-sm" onClick={() => setShowGenerator(true)} data-testid="button-open-generator">Open generator</Button></div>}</Card><Card><SectionTitle title="Content calendar" action={<Button variant="secondary" className="btn-sm" data-testid="button-calendar-options"><Calendar /> This week</Button>} /><div className="list">{calendar.map((item, index) => <div className="list-row" key={`${item[0]}-${index}`}><div style={{ width: 55, color: '#938c83', fontSize: 10 }}>{item[0]}</div><div className="row-main"><div className="row-title">{item[1]}</div><div className="row-copy">{item[2]}</div></div><Badge tone={item[3] === 'Scheduled' ? 'success' : item[3] === 'Needs approval' ? 'warning' : 'neutral'}>{item[3]}</Badge></div>)}</div></Card></div>{showGenerator && <Modal title="Generate content" description="Your AI CMO will use your brand voice and current audience signals." onClose={() => setShowGenerator(false)}><form onSubmit={generate}><div className="form-grid"><div className="field full"><label>What should we create?</label><textarea name="prompt" defaultValue="Create a warm Instagram post about the new harvest." /></div><div className="field"><label>Platform</label><select defaultValue="Instagram"><option>Instagram</option><option>Facebook</option><option>LinkedIn</option></select></div><div className="field"><label>Tone</label><select defaultValue="Warm and grounded"><option>Warm and grounded</option><option>Educational</option><option>Playful</option></select></div><div className="field"><label>Audience</label><input defaultValue="Local families and food lovers" /></div><div className="field"><label>Content type</label><select defaultValue="Reel"><option>Reel</option><option>Carousel</option><option>Post</option></select></div></div><div className="modal-foot"><Button type="button" onClick={() => setShowGenerator(false)}>Cancel</Button><Button variant="primary" type="submit" data-testid="button-submit-generator">Generate draft</Button></div></form></Modal>}</>;
}

function Agents() {
  const [agents, setAgents] = useStored('ai-os-agents', [
    { name: 'AI Business Manager', role: 'Coordinates the entire system', tasks: 18, success: '98.6%', iconKey: 'manager', active: true, last: '2 min ago' },
    { name: 'AI CMO', role: 'Marketing and content strategy', tasks: 9, success: '96.2%', iconKey: 'cmo', active: true, last: '8 min ago' },
    { name: 'AI Sales', role: 'Leads, qualification, and follow-up', tasks: 14, success: '94.8%', iconKey: 'sales', active: true, last: '4 min ago' },
    { name: 'AI Support', role: 'Customer communication', tasks: 42, success: '99.1%', iconKey: 'support', active: true, last: '1 min ago' },
    { name: 'AI Operations', role: 'Orders, appointments, workflows', tasks: 24, success: '97.4%', iconKey: 'operations', active: true, last: '3 min ago' },
    { name: 'AI Analytics', role: 'Business intelligence and insights', tasks: 7, success: '98.0%', iconKey: 'analytics', active: false, last: '1 hr ago' },
  ]);
  const [filter, setFilter] = useState('All');
  const agentIcons = { manager: Gauge, cmo: Wand2, sales: Target, support: Headphones, operations: Boxes, analytics: BarChart3 };
  return <><PageHeader eyebrow="Virtual team" title="AI employees" subtitle="Manage the agents quietly moving Green Valley Farms forward." action={<Button variant="secondary" onClick={() => setFilter(filter === 'All' ? 'Active' : 'All')} data-testid="button-filter-agents"><Filter /> {filter === 'All' ? 'Show active' : 'Show all'}</Button>} /><div className="grid agent-grid">{agents.filter((agent) => filter === 'All' || agent.active).map((agent, index) => { const Icon = agentIcons[agent.iconKey as keyof typeof agentIcons] ?? Bot; return <Card key={agent.name} className={cx('agent-card', agent.active && 'active')}><div className="agent-head"><div className="agent-identity"><div className="agent-icon"><Icon /></div><div><h3>{agent.name}</h3><div className="agent-role">{agent.role}</div></div></div><button className={cx('switch', agent.active && 'on')} onClick={() => setAgents((items) => items.map((item) => item.name === agent.name ? { ...item, active: !item.active } : item))} aria-label={`Toggle ${agent.name}`} data-testid={`button-toggle-agent-${agent.name.toLowerCase().replaceAll(' ', '-')}`}><i /></button></div><div className="stat-row"><span>Tasks today</span><strong>{agent.tasks}</strong></div><div className="stat-row"><span>Success rate</span><strong>{agent.success}</strong></div><div className="stat-row"><span>Last activity</span><strong>{agent.last}</strong></div><div className="agent-actions"><Button variant="secondary" className="btn-sm" data-testid={`button-configure-agent-${agent.name.toLowerCase().replaceAll(' ', '-')}`}><Settings2 /> Configure</Button><Button variant="soft" className="btn-sm" data-testid={`button-activity-agent-${agent.name.toLowerCase().replaceAll(' ', '-')}`}><Activity /> Activity</Button></div></Card>; })}</div><SectionTitle title="Agent activity" action={<div className="filters"><button className="filter active" data-testid="button-agent-status-completed">Completed</button><button className="filter" data-testid="button-agent-status-awaiting">Awaiting approval</button></div>} /><Card><div className="list">{[['AI Support', "Answered Sarah's question", '10:42 AM', 'Completed'], ['AI Sales', 'Qualified Oak & Field Cafe', '10:45 AM', 'Completed'], ['AI CMO', 'Generated Instagram post', '10:51 AM', 'Awaiting approval'], ['AI Operations', 'Confirmed Order #10482', '10:54 AM', 'Completed']].map(([agent, action, time, status]) => <div className="list-row" key={action}><div className="activity-dot" /><div className="row-main"><div className="row-title">{agent} <span className="row-copy" style={{ display: 'inline' }}>· {action}</span></div><div className="row-copy">{time}</div></div><Badge tone={status === 'Completed' ? 'success' : 'warning'}>{status}</Badge></div>)}</div></Card></>;
}

function Automations() {
  const [selected, setSelected] = useState(0);
  const [enabled, setEnabled] = useStored('ai-os-workflow-enabled', false);
  const [test, setTest] = useState<'idle' | 'running' | 'success' | 'error'>('idle');
  const recipes = [{ name: 'WhatsApp order flow', copy: 'Understand orders, check stock, create an order, and confirm.', icon: MessageCircle }, { name: 'Lead qualification', copy: 'Score new leads and alert sales when intent is high.', icon: Target }, { name: 'Inactive customer', copy: 'Find customers inactive for 60 days and prepare an offer.', icon: RefreshCw }];
  const runTest = () => { setTest('running'); window.setTimeout(() => setTest('success'), 850); };
  return <><PageHeader eyebrow="Operations" title="Automation recipes" subtitle="Give repetitive work a dependable path and a clear owner." action={<Button variant="primary" onClick={() => setSelected(3)} data-testid="button-new-workflow"><Plus /> New workflow</Button>} /><div className="grid" style={{ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }}>{recipes.map((recipe, index) => { const Icon = recipe.icon; return <Card key={recipe.name} className={cx(selected === index && 'recommendation')}><div className="integration-icon"><Icon /></div><h2>{recipe.name}</h2><p className="subtle" style={{ minHeight: 38 }}>{recipe.copy}</p><div className="toolbar" style={{ marginTop: 15 }}><Button variant="secondary" className="btn-sm" onClick={() => setSelected(index)} data-testid={`button-open-workflow-${index}`}>Open workflow <ArrowRight /></Button><Badge tone={index === 0 ? 'success' : 'neutral'}>{index === 0 ? 'Enabled' : 'Draft'}</Badge></div></Card>; })}</div><SectionTitle title={selected === 3 ? 'New workflow' : recipes[selected].name} action={<div className="toolbar"><Badge tone={enabled ? 'success' : 'neutral'}>{enabled ? 'Enabled' : 'Draft'}</Badge><Button variant="secondary" className="btn-sm" onClick={() => setTest('idle')} data-testid="button-save-workflow"><Check /> Save</Button><Button variant="soft" className="btn-sm" onClick={runTest} data-testid="button-test-workflow"><TestTube2 /> Test</Button><Button variant={enabled ? 'danger' : 'green'} className="btn-sm" onClick={() => setEnabled((v) => !v)} data-testid="button-enable-workflow">{enabled ? 'Disable' : 'Enable'}</Button></div>} /><Card><div className="workflow-nodes">{[['Trigger', 'New WhatsApp message', MessageCircle], ['AI decision', 'Understand request', Bot], ['Condition', 'Product in stock?', SlidersHorizontal], ['Action', 'Create order', Database], ['Notification', 'Send confirmation', Bell]].map(([name, copy, Icon], index) => <div key={name as string} style={{ display: 'flex', alignItems: 'center' }}><div className="workflow-node"><div className="integration-icon" style={{ marginBottom: 9 }}><Icon /></div><strong>{name as string}</strong><span>{copy as string}</span></div>{index < 4 && <div className="connector" />}</div>)}</div><div style={{ borderTop: '1px solid #eeeae5', paddingTop: 16 }}><div className="eyebrow">Test mode</div>{test === 'idle' && <p className="subtle">Run a safe test before enabling this workflow. No customer messages will be sent.</p>}{test === 'running' && <div className="test-list">{['Trigger', 'AI Decision', 'Database', 'API', 'Action'].map((step) => <div className="test-step" key={step}><RefreshCw size={15} /> Checking {step}...</div>)}</div>}{test === 'success' && <div className="test-list">{['Trigger', 'AI Decision', 'Database', 'API', 'Action'].map((step) => <div className="test-step" key={step}><CheckCircle2 /> {step} passed</div>)}<div className="ai-banner" style={{ marginTop: 10 }}><CheckCircle2 /> Test completed successfully. Ready to enable.</div></div>}{test === 'error' && <div className="ai-banner" style={{ background: '#fff3f1', borderColor: '#f2d8d4', color: '#b34141' }}><AlertCircle /> API connection failed. Reconnect the integration and retry.</div>}</div></Card></>;
}

function Approvals() {
  const [items, setItems] = useStored('ai-os-all-approvals', [
    { id: 1, agent: 'AI Sales', title: 'Send 10% discount to Sarah Mitchell', reason: 'Sarah is a high-LTV customer and has not purchased for 45 days.', status: 'Pending' },
    { id: 2, agent: 'AI CMO', title: 'Publish Instagram harvest post', reason: 'Harvest content is trending 32% above your average engagement.', status: 'Pending' },
    { id: 3, agent: 'AI Operations', title: 'Refund Order #10471', reason: 'Customer requested a refund after a delivery delay.', status: 'Approved' },
  ]);
  const [filter, setFilter] = useState('Pending');
  const action = (id: number, status: string) => setItems((current) => current.map((item) => item.id === id ? { ...item, status } : item));
  const shown = items.filter((item) => item.status === filter);
  return <><PageHeader eyebrow="Human oversight" title="Approval center" subtitle="High-impact actions stay visible and under your control." action={<Badge tone="warning">{items.filter((item) => item.status === 'Pending').length} pending</Badge>} /><div className="tabs">{['Pending', 'Approved', 'Rejected', 'Expired'].map((item) => <button className={cx('tab', filter === item && 'active')} key={item} onClick={() => setFilter(item)} data-testid={`button-approval-filter-${item.toLowerCase()}`}>{item} <span style={{ color: '#aaa49c' }}>{items.filter((approval) => approval.status === item).length}</span></button>)}</div><div>{shown.map((item) => <Card className="approval-card" key={item.id}><div className="approval-top"><div><div className="eyebrow">{item.agent}</div><h2>{item.title}</h2></div><Badge tone={item.status === 'Pending' ? 'warning' : item.status === 'Approved' ? 'success' : 'danger'}>{item.status}</Badge></div><div className="approval-reason"><strong>AI reasoning</strong><div style={{ marginTop: 4 }}>{item.reason}</div></div>{item.status === 'Pending' && <div className="toolbar"><Button variant="green" className="btn-sm" onClick={() => action(item.id, 'Approved')} data-testid={`button-approve-${item.id}`}><Check /> Approve</Button><Button variant="secondary" className="btn-sm" data-testid={`button-edit-approval-${item.id}`}><Pencil /> Edit</Button><Button variant="danger" className="btn-sm" onClick={() => action(item.id, 'Rejected')} data-testid={`button-reject-${item.id}`}><X /> Reject</Button></div>}</Card>)}{!shown.length && <Card><div className="empty"><ClipboardCheck /><h3>No {filter.toLowerCase()} actions</h3><p>Every AI action will appear here when it needs your attention.</p></div></Card>}</div></>;
}

function Opportunities() {
  const [reviewed, setReviewed] = useStored<number[]>('ai-os-reviewed-opportunities', []);
  const opportunities = [{ id: 1, title: '12 inactive customers represent $2,400 in historical revenue.', copy: 'A personal reactivation sequence could bring high-value customers back without broad discounting.', cat: 'Revenue', tone: '' }, { id: 2, title: 'Harvest content is gaining traction.', copy: 'Engagement for harvest content is 32% above your average. Create a short Reel while the signal is strong.', cat: 'Marketing', tone: 'green' }, { id: 3, title: 'Lead conversion decreased 14% this week.', copy: 'High-intent leads are waiting longer than usual for a first response. Tighten the follow-up window.', cat: 'Problem', tone: 'brown' }]; 
  return <><PageHeader eyebrow="Intelligence" title="AI opportunities" subtitle="The moments your AI team believes are worth your attention." action={<Button variant="secondary" onClick={() => setReviewed([])} data-testid="button-refresh-opportunities"><RefreshCw /> Refresh insights</Button>} /><div className="grid">{opportunities.map((item) => <Card key={item.id} className={cx('opportunity', item.tone)}><div className="eyebrow">{item.cat}</div><h2>{item.title}</h2><p className="opportunity-copy">{item.copy}</p><div className="toolbar">{reviewed.includes(item.id) ? <Badge tone="success"><Check /> Reviewed</Badge> : <><Button variant="secondary" className="btn-sm" onClick={() => setReviewed((items) => [...items, item.id])} data-testid={`button-review-opportunity-${item.id}`}>Review</Button><Button variant="primary" className="btn-sm" onClick={() => setReviewed((items) => [...items, item.id])} data-testid={`button-create-opportunity-${item.id}`}>Create action <ArrowRight /></Button></>}</div></Card>)}</div></>;
}

function Analytics() {
  const [range, setRange] = useState('Last 30 days');
  const bars = [42, 55, 48, 63, 59, 76, 88, 69, 82, 96, 78, 93];
  return <><PageHeader eyebrow="Intelligence" title="Business analytics" subtitle="See the signals beneath the day-to-day." action={<select className="business-select" value={range} onChange={(e) => setRange(e.target.value)} data-testid="select-analytics-range"><option>Last 7 days</option><option>Last 30 days</option><option>Last 90 days</option></select>} /><div className="grid kpi-grid"><Kpi title="Revenue" value="$38,420" foot="+18% vs prior period" icon={<TrendingUp />} tone="green" /><Kpi title="Orders" value="312" foot="+14% vs prior period" icon={<ShoppingBag />} tone="orange" /><Kpi title="Customers" value="184" foot="+22 new this period" icon={<Users />} tone="brown" /><Kpi title="Conversion" value="4.2%" foot="-0.4% vs prior period" icon={<Target />} tone="rose" /></div><div className="grid split-grid"><Card className="chart-box"><SectionTitle title="Revenue over time" action={<span>{range}</span>} /><div className="chart">{bars.map((height, index) => <div className="bar-col" key={index}><i style={{ height: `${height}%` }} /><span>{['Mar 1', '', 'Mar 5', '', 'Mar 9', '', 'Mar 13', '', 'Mar 17', '', 'Mar 21', ''][index]}</span></div>)}</div></Card><Card className="chart-box"><SectionTitle title="Sales by category" /><div className="donut"><div className="donut-center"><strong>$38.4k</strong>Total revenue</div></div><div className="legend"><span><i style={{ background: '#15803d' }} />Eggs 42%</span><span><i style={{ background: '#f97316' }} />Honey 25%</span><span><i style={{ background: '#9bc9a6' }} />Tomatoes 17%</span><span><i style={{ background: '#e9d4bd' }} />Other 16%</span></div></Card></div><div className="grid split-grid"><Card><SectionTitle title="Acquisition sources" /><div className="list">{[['WhatsApp', 78, '42%'], ['Instagram', 61, '27%'], ['Website', 44, '18%'], ['Referral', 26, '9%'], ['Other', 13, '4%']].map(([name, value, percent]) => <div className="list-row" key={name as string}><div className="row-main"><div className="row-title">{name}</div><div className="bar" style={{ marginTop: 6 }}><i style={{ width: `${value}%`, background: '#73ad80' }} /></div></div><strong style={{ fontSize: 11 }}>{percent}</strong></div>)}</div></Card><Card className="recommendation"><SectionTitle title="AI analysis" action={<Sparkles size={16} />} /><div className="recommendation-item"><div className="rec-title">Revenue increased 18%.</div><div className="rec-copy">Orders are growing across every channel, led by Fresh Eggs.</div></div><div className="recommendation-item"><div className="rec-title">Conversion decreased 4%.</div><div className="rec-copy">High-intent leads are waiting longer for first contact than your healthy baseline.</div><Button variant="green" className="btn-sm" data-testid="button-analytics-recommendation">Review follow-up queue</Button></div></Card></div></>;
}

function Integrations() {
  const [connected, setConnected] = useStored<string[]>('ai-os-integrations', ['Email', 'Stripe']);
  const groups = [{ title: 'Communication', items: [['WhatsApp', 'Customer conversations and order updates', MessageCircle], ['Instagram', 'Content publishing and engagement', Globe2], ['Facebook', 'Social publishing and replies', Globe2], ['Email', 'Inbox and customer updates', Mail]] }, { title: 'Commerce', items: [['Shopify', 'Products, inventory, and orders', ShoppingBag], ['Stripe', 'Payments and transaction status', Archive]] }, { title: 'Productivity & CRM', items: [['Google Calendar', 'Appointments and availability', Calendar], ['HubSpot', 'Leads and customer records', Users]] }];
  return <><PageHeader eyebrow="Workspace connections" title="Connect your tools" subtitle="Bring the systems your business already uses into one operating view." /><div className="grid integration-grid">{groups.flatMap((group) => group.items.map(([name, description, Icon]) => { const active = connected.includes(name as string); return <Card className="integration-card" key={name as string}><div className="integration-icon"><Icon /></div><h2>{name as string}</h2><p className="integration-desc">{description as string}</p>{active ? <div className="toolbar"><Badge tone="success"><Check /> Connected</Badge><Button variant="secondary" className="btn-sm" onClick={() => setConnected((items) => items.filter((item) => item !== name))} data-testid={`button-disconnect-${String(name).toLowerCase().replace(' ', '-')}`}>Disconnect</Button></div> : <Button variant="soft" className="btn-sm" onClick={() => setConnected((items) => [...items, name as string])} data-testid={`button-connect-${String(name).toLowerCase().replace(' ', '-')}`}>Connect <ArrowRight /></Button>}</Card>; }))}</div></>;
}

function BusinessBrain() {
  const [sources, setSources] = useStored('ai-os-sources', [['Company Guide.pdf', 'Document', 'Processed'], ['Product Catalog.csv', 'Catalog', 'Processed'], ['Green Valley website', 'Website', 'Synced']] as string[][]);
  const [query, setQuery] = useState('');
  const [answer, setAnswer] = useState(false);
  const searchBrain = () => { if (query.trim()) setAnswer(true); };
  const upload = () => { setSources((items) => [...items, ['New business document.txt', 'Document', 'Processing']]); window.setTimeout(() => setSources((items) => items.map((item, index) => index === items.length - 1 ? [item[0], item[1], 'Processed'] : item)), 1400); };
  return <><PageHeader eyebrow="Knowledge layer" title="Business Brain" subtitle="The context your AI team uses to make grounded decisions." action={<Button variant="primary" onClick={upload} data-testid="button-upload-source"><CloudUpload /> Upload source</Button>} /><div className="brain-layout"><Card><SectionTitle title="Knowledge sources" action={<Badge tone="success">{sources.length} active</Badge>} /><div className="list">{sources.map((source, index) => <div className="source-row" key={`${source[0]}-${index}`}><div className="source-icon">{source[1] === 'Website' ? <Globe2 /> : source[1] === 'Catalog' ? <Database /> : <FileText />}</div><div className="row-main"><div className="row-title">{source[0]}</div><div className="row-copy">{source[1]} · Added to Business Brain</div></div><span className="source-status">{source[2] === 'Processing' ? 'Processing…' : <><Check size={13} /> {source[2]}</>}</span></div>)}</div><div className="empty" style={{ minHeight: 110, paddingBottom: 0 }}><Upload /><p>PDF, DOCX, TXT, and CSV files are supported.</p></div></Card><Card><SectionTitle title="Search your brain" action={<Brain size={16} color="#4b9e61" />} /><div className="search-box" style={{ width: '100%' }}><Search /><input style={{ width: '100%' }} value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') searchBrain(); }} placeholder="What is our return policy?" data-testid="input-brain-search" /><Button variant="green" className="btn-sm" onClick={searchBrain} data-testid="button-search-brain">Search</Button></div>{answer ? <div className="answer"><div className="eyebrow">Answer</div><div className="row-title">Returns are accepted within 14 days when products are unopened and kept chilled. Our team will arrange a pickup or issue store credit.</div><div className="confidence">Confidence · 96% · Source: Company Guide.pdf</div></div> : <div className="empty" style={{ minHeight: 180 }}><Search /><h3>Ask your business anything</h3><p>Search policies, products, FAQs, and brand guidelines.</p></div>}</Card></div></>;
}

function Settings() {
  const [tab, setTab] = useState('Business profile');
  const [saved, setSaved] = useState(false);
  const tabs = ['Business profile', 'Products', 'Brand voice', 'Team', 'AI controls'];
  return <><PageHeader eyebrow="Workspace administration" title="Settings" subtitle="Shape how your business and AI team work together." action={<Button variant="primary" onClick={() => setSaved(true)} data-testid="button-save-settings"><Check /> Save changes</Button>} />{saved && <div className="ai-banner"><CheckCircle2 />Settings saved to this workspace.<button className="close-btn" onClick={() => setSaved(false)}><X size={14} /></button></div>}<div className="settings-layout"><div className="settings-nav">{tabs.map((item) => <button className={cx(tab === item && 'active')} onClick={() => setTab(item)} key={item} data-testid={`button-settings-${item.toLowerCase().replaceAll(' ', '-')}`}>{item}</button>)}</div><Card><div className="tabs"><button className="tab active">{tab}</button></div>{tab === 'Business profile' && <div className="form-grid"><div className="field full"><label>Business name</label><input defaultValue="Green Valley Farms" /></div><div className="field"><label>Industry</label><input defaultValue="Farm & food production" /></div><div className="field"><label>Timezone</label><select defaultValue="Pacific Time"><option>Pacific Time</option><option>Mountain Time</option><option>Eastern Time</option></select></div><div className="field"><label>Currency</label><select defaultValue="USD · $"><option>USD · $</option><option>CAD · $</option></select></div><div className="field"><label>Website</label><input defaultValue="greenvalleyfarms.co" /></div></div>}{tab === 'Products' && <div className="list">{[['Organic Tomatoes', '$4 / kg', 'In stock'], ['Fresh Eggs', '$6 / dozen', 'In stock'], ['Raw Honey', '$12 / jar', 'In stock']].map((product) => <div className="list-row" key={product[0]}><div className="row-main"><div className="row-title">{product[0]}</div><div className="row-copy">{product[1]}</div></div><Badge tone="success">{product[2]}</Badge><button className="icon-btn" data-testid={`button-edit-product-${product[0].slice(0, 4)}`}><Edit3 /></button></div>)}</div>}{tab === 'Brand voice' && <div className="form-grid"><div className="field full"><label>Personality</label><textarea defaultValue="Warm, grounded, and useful. Speak like a thoughtful neighbor who knows the land and respects the customer’s time." /></div><div className="field"><label>Tone</label><select defaultValue="Warm and clear"><option>Warm and clear</option><option>Confident and direct</option><option>Friendly and playful</option></select></div><div className="field"><label>Keywords</label><input defaultValue="fresh, local, honest, seasonal" /></div><div className="field full"><label>Words to avoid</label><input defaultValue="cheap, best ever, hurry, guaranteed" /></div></div>}{tab === 'Team' && <div className="list">{[['Alexandra Andria', 'Owner', 'Full access'], ['Sam Rivera', 'Manager', 'Orders, customers, AI'], ['Tina Brooks', 'Viewer', 'Analytics only']].map((member) => <div className="list-row" key={member[0]}><Avatar name={member[0]} /><div className="row-main"><div className="row-title">{member[0]}</div><div className="row-copy">{member[1]}</div></div><Badge tone="neutral">{member[2]}</Badge><button className="icon-btn" data-testid={`button-edit-member-${member[0].slice(0, 3)}`}><Edit3 /></button></div>)}<Button variant="soft" className="btn-sm" data-testid="button-invite-member"><Plus /> Invite teammate</Button></div>}{tab === 'AI controls' && <div><div className="toggle-row"><div className="toggle-copy"><strong>Proactive messaging</strong><span>Let AI prepare follow-ups when a customer signal is clear.</span></div><Switch defaultOn /></div><div className="toggle-row"><div className="toggle-copy"><strong>Require approval for discounts</strong><span>Keep offers over $10 under human review.</span></div><Switch defaultOn /></div><div className="field" style={{ marginTop: 18 }}><label>Default autonomy level</label><select defaultValue="Approval"><option>Suggest</option><option>Approval</option><option>Autonomous</option></select></div><div className="eyebrow" style={{ marginTop: 24 }}>Agent permissions</div><div className="permission-list">{['Read customer', 'Read orders', 'Answer FAQ', 'Send basic replies', 'Create CRM records', 'Manage discounts'].map((item, index) => <label className="permission" key={item}>{item}<input type="checkbox" defaultChecked={index < 5} /></label>)}</div></div>}</Card></div></>;
}
function Switch({ defaultOn }: { defaultOn?: boolean }) { const [on, setOn] = useState(!!defaultOn); return <button className={cx('switch', on && 'on')} onClick={() => setOn((v) => !v)} aria-label="Toggle setting" data-testid={`button-toggle-setting-${on ? 'on' : 'off'}`}><i /></button>; }

function IndustryWorkspace() {
  const { activeBusiness } = useBusiness();
  const industry = activeBusiness?.industry ?? 'Other';
  const isRealEstate = industry === 'Real Estate';
  const title = isRealEstate ? 'Properties & listings' : industry === 'E-commerce' ? 'Products' : 'Inventory & harvest';
  const eyebrow = isRealEstate ? 'Real estate workspace' : industry === 'E-commerce' ? 'Commerce workspace' : 'Operations workspace';
  return <><PageHeader eyebrow={eyebrow} title={title} subtitle={`Keep ${activeBusiness?.name ?? 'your business'} moving with a clear view of what is available.`} action={<Button variant="primary" data-testid="button-add-industry-item"><Plus /> Add {isRealEstate ? 'listing' : 'item'}</Button>} /><Card className="table-card" pad={false}><div className="table-toolbar"><div><div className="eyebrow">Live catalog</div><h2>{activeBusiness?.products.length ?? 0} active items</h2></div><Badge tone="success">Synced to AI team</Badge></div><div className="table-scroll"><table><thead><tr><th>{isRealEstate ? 'Listing' : 'Name'}</th><th>{isRealEstate ? 'Price' : 'Price'}</th><th>Availability</th><th>AI readiness</th></tr></thead><tbody>{(activeBusiness?.products ?? []).map((product) => <tr key={product.id}><td><strong>{product.name}</strong></td><td>{money(product.price)}</td><td><Badge tone={product.availability.toLowerCase().includes('stock') || product.availability.toLowerCase().includes('available') ? 'success' : 'warning'}>{product.availability}</Badge></td><td><Badge tone="info"><Check size={12} /> Known by AI team</Badge></td></tr>)}</tbody></table>{!activeBusiness?.products.length && <div className="empty"><Package /><h3>No items configured</h3><p>Add products or services from onboarding or Settings to give your AI team context.</p></div>}</div></Card></>;
}

function Home() { const [, setLocation] = useLocation(); useEffect(() => { setLocation('/dashboard'); }, [setLocation]); return <div className="empty"><Sparkles /><h3>Opening your command room</h3><p>Loading your business workspace…</p></div>; }
function NotFound() { return <div className="empty" style={{ minHeight: '80dvh' }}><AlertCircle /><h3>That page is not on the map</h3><p>Return to the dashboard to continue.</p><Link href="/dashboard" className="btn btn-green" data-testid="link-back-dashboard">Back to dashboard</Link></div>; }
function RoutedErrorBoundary({ children }: { children: ReactNode }) { const [location] = useLocation(); return <ErrorBoundary resetKey={location}>{children}</ErrorBoundary>; }
function Router() { return <RoutedErrorBoundary><WouterSwitch><Route path="/" component={Home} /><Route path="/onboarding" component={Onboarding} /><Route path="/dashboard" component={Dashboard} /><Route path="/command" component={CommandCenter} /><Route path="/command-center" component={CommandCenter} /><Route path="/conversations" component={Conversations} /><Route path="/orders" component={Orders} /><Route path="/customers" component={Customers} /><Route path="/crm" component={CRM} /><Route path="/cmo" component={Marketing} /><Route path="/marketing" component={Marketing} /><Route path="/agents" component={Agents} /><Route path="/automations" component={Automations} /><Route path="/approvals" component={Approvals} /><Route path="/opportunities" component={Opportunities} /><Route path="/analytics" component={Analytics} /><Route path="/integrations" component={Integrations} /><Route path="/brain" component={BusinessBrain} /><Route path="/inventory" component={IndustryWorkspace} /><Route path="/properties" component={IndustryWorkspace} /><Route path="/products" component={IndustryWorkspace} /><Route path="/settings" component={Settings} /><Route component={NotFound} /></WouterSwitch></RoutedErrorBoundary>; }
function RoutedApp() { const [location] = useLocation(); return location === '/onboarding' ? <Router /> : <Shell><Router /></Shell>; }
function App() { return <QueryClientProvider client={queryClient}><TooltipProvider><WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, '')}><BusinessProvider><RoutedApp /></BusinessProvider></WouterRouter><Toaster /></TooltipProvider></QueryClientProvider>; }
export default App;