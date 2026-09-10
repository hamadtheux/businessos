import { useBusiness } from "@/business-context";
import { CreatePublishPage } from "@/features/marketing/create-publish-page";

export function CmoPage() {
  const { activeBusinessId } = useBusiness();

  return <CreatePublishPage key={activeBusinessId || "no-business"} />;
}
