import { Workspace } from "@/components/workspace/Workspace";

export default async function RepoPage(props: PageProps<"/repos/[id]">) {
  const { id } = await props.params;
  return <Workspace repoId={Number(id)} />;
}
