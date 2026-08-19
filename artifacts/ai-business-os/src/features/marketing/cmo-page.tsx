import { useState, type FormEvent, type ReactNode } from "react";
import {
  BarChart3,
  Calendar,
  Check,
  Globe2,
  RefreshCw,
  Target,
  TrendingUp,
  Wand2,
} from "lucide-react";
import { useBusiness } from "@/business-context";
import {
  Badge,
  Button,
  Card,
  Modal,
  PageHeader,
  SectionTitle,
} from "@/components/product-ui";
import { useWorkspaceData } from "@/hooks/use-workspace-data";
import { CmoDepartmentNav } from "@/features/marketing/marketing-pages";

function Kpi({
  title,
  value,
  foot,
  icon,
  tone,
}: {
  title: string;
  value: string;
  foot: string;
  icon: ReactNode;
  tone: string;
}) {
  return (
    <Card className="kpi">
      <div className="kpi-top">
        <span>{title}</span>
        <div className={`kpi-icon ${tone}`}>{icon}</div>
      </div>
      <div className="kpi-value">{value}</div>
      <div className="kpi-foot">
        <span className={tone === "rose" ? "trend-down" : "trend-up"}>
          {tone === "rose" ? "8 high intent" : foot.split(" ")[0]}
        </span>
        <span>{foot}</span>
      </div>
    </Card>
  );
}

const fallbackDays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"];

export function CmoPage() {
  const { activeBusiness } = useBusiness();
  const { data, update } = useWorkspaceData();
  const isRealEstate = activeBusiness?.industry === "Real Estate";
  const activeTab =
    new URLSearchParams(window.location.search).get("tab") ?? "Overview";
  const [showGenerator, setShowGenerator] = useState(false);
  const [generated, setGenerated] = useState(false);
  const [calendarView, setCalendarView] = useState("This week");
  const primaryPost = data.socialPosts[0];
  const approved =
    primaryPost?.reviewStatus === "Approved" ||
    primaryPost?.status === "Scheduled";

  const generate = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setShowGenerator(false);
    setGenerated(true);
  };

  const updatePrimaryPost = (schedule: boolean) => {
    if (!primaryPost) return;
    update((current) => ({
      ...current,
      socialPosts: current.socialPosts.map((post) =>
        post.id === primaryPost.id
          ? {
              ...post,
              reviewStatus: "Approved",
              ...(schedule
                ? {
                    status: "Scheduled" as const,
                    schedule:
                      post.schedule === "Unscheduled"
                        ? "Tomorrow · 10:00 AM"
                        : post.schedule,
                  }
                : {}),
            }
          : post,
      ),
    }));
  };

  return (
    <>
      <PageHeader
        eyebrow="AI CMO"
        title="AI Marketing Manager"
        subtitle="A steady stream of useful content, ready for your review."
        action={
          <Button
            variant="primary"
            onClick={() => setShowGenerator(true)}
            data-testid="button-generate-content"
          >
            <Wand2 /> Generate content
          </Button>
        }
      />
      <CmoDepartmentNav active={activeTab} />
      <div className="grid kpi-grid">
        <Kpi
          title="Reach this month"
          value={isRealEstate ? "24.8k" : "18.4k"}
          foot="+21% from last month"
          icon={<Globe2 />}
          tone="green"
        />
        <Kpi
          title="Engagement"
          value={isRealEstate ? "8.1%" : "6.8%"}
          foot="+32% from last month"
          icon={<TrendingUp />}
          tone="orange"
        />
        <Kpi
          title="New leads"
          value={String(data.analytics.leads)}
          foot="+8 from campaigns"
          icon={<Target />}
          tone="brown"
        />
        <Kpi
          title="Conversion"
          value={`${data.analytics.conversion}%`}
          foot="+0.8% from last month"
          icon={<BarChart3 />}
          tone="rose"
        />
      </div>
      <div className="grid split-grid">
        <Card>
          <SectionTitle
            title="Content preview"
            action={
              generated ? (
                <Badge tone={approved ? "success" : "warning"}>
                  {approved ? "Approved" : "Needs review"}
                </Badge>
              ) : (
                <Badge>Ready when you are</Badge>
              )
            }
          />
          {generated ? (
            <>
              <div
                className="card"
                style={{
                  padding: 18,
                  background: "#f9f6ef",
                  borderColor: "#eee3d1",
                }}
              >
                <div className="eyebrow">
                  Instagram Reel ·{" "}
                  {isRealEstate ? "Oak Hills Home" : "Spring harvest"}
                </div>
                <h2 style={{ lineHeight: 1.5 }}>
                  {primaryPost?.content ??
                    (isRealEstate
                      ? "Slow Saturday mornings start here. Oak Hills brings light, space, and a neighborhood that feels settled from day one."
                      : "Fresh mornings start here. Our new harvest is picked with care, packed within hours, and headed to your table.")}
                </h2>
                <p className="subtle" style={{ marginTop: 12 }}>
                  {isRealEstate
                    ? "#AustinHomes #OakHills"
                    : "#GreenValleyFarms #FromTheFarm"}
                </p>
              </div>
              <div className="toolbar" style={{ marginTop: 13 }}>
                <Button
                  variant="secondary"
                  className="btn-sm"
                  onClick={() => setGenerated(false)}
                  data-testid="button-regenerate-content"
                >
                  <RefreshCw /> Regenerate
                </Button>
                <Button
                  variant="soft"
                  className="btn-sm"
                  onClick={() => updatePrimaryPost(false)}
                  data-testid="button-approve-content"
                >
                  <Check /> Approve
                </Button>
                <Button
                  variant="primary"
                  className="btn-sm"
                  onClick={() => updatePrimaryPost(true)}
                  data-testid="button-schedule-content"
                >
                  <Calendar /> Schedule
                </Button>
              </div>
            </>
          ) : (
            <div className="empty">
              <Wand2 />
              <h3>No draft open</h3>
              <p>
                Give the CMO a prompt and it will prepare a post in your brand
                voice.
              </p>
              <Button
                variant="soft"
                className="btn-sm"
                onClick={() => setShowGenerator(true)}
                data-testid="button-open-generator"
              >
                Open generator
              </Button>
            </div>
          )}
        </Card>
        <Card>
          <SectionTitle
            title="Content calendar"
            action={
              <Button
                variant="secondary"
                className="btn-sm"
                onClick={() =>
                  setCalendarView((current) =>
                    current === "This week" ? "Next week" : "This week",
                  )
                }
                data-testid="button-calendar-options"
              >
                <Calendar /> {calendarView}
              </Button>
            }
          />
          <div className="list">
            {data.socialPosts.slice(0, 5).map((post, index) => (
              <div className="list-row" key={post.id}>
                <div style={{ width: 55, color: "#938c83", fontSize: 10 }}>
                  {post.calendarDay ?? fallbackDays[index]}
                </div>
                <div className="row-main">
                  <div className="row-title">
                    {post.contentType ?? `${post.platform} Post`}
                  </div>
                  <div className="row-copy">{post.content}</div>
                </div>
                <Badge
                  tone={
                    post.status === "Scheduled" || post.status === "Published"
                      ? "success"
                      : post.status === "Needs approval"
                        ? "warning"
                        : "neutral"
                  }
                >
                  {post.status}
                </Badge>
              </div>
            ))}
          </div>
        </Card>
      </div>
      {showGenerator && (
        <Modal
          title="Generate content"
          description="Your AI CMO will use your brand voice and current audience signals."
          onClose={() => setShowGenerator(false)}
        >
          <form onSubmit={generate}>
            <div className="form-grid">
              <div className="field full">
                <label>What should we create?</label>
                <textarea
                  name="prompt"
                  defaultValue={
                    isRealEstate
                      ? "Create a confident Instagram post for the Oak Hills private viewing weekend."
                      : "Create a warm Instagram post about the new harvest."
                  }
                />
              </div>
              <div className="field">
                <label>Platform</label>
                <select defaultValue="Instagram">
                  <option>Instagram</option>
                  <option>Facebook</option>
                  <option>LinkedIn</option>
                </select>
              </div>
              <div className="field">
                <label>Tone</label>
                <select defaultValue="Warm and grounded">
                  <option>Warm and grounded</option>
                  <option>Educational</option>
                  <option>Playful</option>
                </select>
              </div>
              <div className="field">
                <label>Audience</label>
                <input
                  defaultValue={
                    isRealEstate
                      ? "Pre-approved family buyers in Austin"
                      : "Local families and food lovers"
                  }
                />
              </div>
              <div className="field">
                <label>Content type</label>
                <select defaultValue="Reel">
                  <option>Reel</option>
                  <option>Carousel</option>
                  <option>Post</option>
                </select>
              </div>
            </div>
            <div className="modal-foot">
              <Button type="button" onClick={() => setShowGenerator(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                type="submit"
                data-testid="button-submit-generator"
              >
                Generate draft
              </Button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
