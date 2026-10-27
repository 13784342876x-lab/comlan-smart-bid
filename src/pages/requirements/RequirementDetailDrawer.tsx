import { getProjectRequirement } from "../../api/projects";
import { useApiQuery } from "../../api/useApiQuery";
import { RequirementTrace } from "../../components/domain/RequirementTrace";
import { Drawer } from "../../components/ui/Drawer";
import { ErrorState, LoadingState } from "../../components/ui/PageState";

export function RequirementDetailDrawer({ projectId, requirementId, onClose }: { projectId: string; requirementId: string; onClose: () => void }) {
  const query = useApiQuery((signal) => getProjectRequirement(projectId, requirementId, signal), [projectId, requirementId]);
  return <Drawer title={query.data?.requirement.title ?? requirementId} onClose={onClose}>{query.loading ? <LoadingState label="正在读取完整追踪链路…" /> : null}{query.error ? <ErrorState error={query.error} onRetry={query.reload} /> : null}{query.data ? <RequirementTrace value={query.data} /> : null}</Drawer>;
}
