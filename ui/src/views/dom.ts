export function byId<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`缺少 UI 节点：${id}`);
  return node as T;
}

export function replaceText(node: HTMLElement, value: string): void {
  node.replaceChildren(document.createTextNode(value));
}
