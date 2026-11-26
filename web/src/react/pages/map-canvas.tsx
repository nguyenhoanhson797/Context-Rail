/* The canvas view of the project map. It draws the tree the engine's `map`
   read resolved: placement, markers and rollups arrive ready, the canvas only
   lays them out left to right. Dropping a node on another proposes a branch
   move; the page confirms it and the engine decides (`link --replace`). */
import { memo, useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from 'react';
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Panel,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { ChevronDown, GitBranch, Target } from '../icons';
import { cn } from '../ui';
import { MARKER_LABEL, type MapNode } from './map';

const COL = 296;
const ROW = 96;
const WIDTH = 252;
const ROOT_GAP = 0.5;

interface CardData extends Record<string, unknown> {
  node: MapNode;
  markers: string[];
  children: number;
  open: boolean;
  hit: boolean;
  toggle: (slug: string) => void;
}

type CardNode = Node<CardData, 'rail'>;

export interface MapCanvasProps {
  nodes: MapNode[];
  roots: string[];
  kids: Map<string, MapNode[]>;
  opened: Set<string>;
  visible: Set<string> | null;
  hits: Set<string>;
  changed: Set<string>;
  needs: Map<string, string[]>;
  selected: string;
  theme: 'light' | 'dark' | 'system';
  canMove: boolean;
  select: (slug: string) => void;
  toggle: (slug: string) => void;
  propose: (src: string, dst: string) => void;
}

export default function MapCanvas(props: MapCanvasProps) {
  return (
    <ReactFlowProvider>
      <Canvas {...props} />
    </ReactFlowProvider>
  );
}

const Card = memo(function Card({ data, selected }: NodeProps<CardNode>) {
  const { node, markers, children, open, hit, toggle } = data;
  const share = node.rollup?.total ? Math.round((node.rollup.complete / node.rollup.total) * 100) : 0;
  return (
    <div
      className={cn(
        'rc-card',
        `cat-${node.category}`,
        markers.includes('focus') && 'focused',
        markers.includes('path') && 'on-path',
        hit && 'hit',
        selected && 'selected',
      )}
    >
      <Handle type="target" position={Position.Left} id="in" isConnectable={false} />
      <Handle type="source" position={Position.Right} id="out" isConnectable={false} />
      <Handle type="target" position={Position.Top} id="dep-in" isConnectable={false} />
      <Handle type="source" position={Position.Bottom} id="dep-out" isConnectable={false} />
      <div className="rc-top">
        <span className="rc-kind">{node.label}</span>
        {node.status ? <span className={cn('rm-status', `cat-${node.category}`)}>{node.status}</span> : null}
      </div>
      <div className="rc-title" title={`${node.title} (${node.slug})`}>{node.title}</div>
      <div className="rc-foot">
        {markers.filter((marker) => marker !== 'path').map((marker) => (
          <span className={cn('rm-mark', `mark-${marker}`)} key={marker}>
            {marker === 'focus' ? <Target size={10} aria-hidden="true" /> : null}
            {MARKER_LABEL[marker] ?? marker}
          </span>
        ))}
        <span className="ra-spacer" />
        {node.rollup ? (
          <span className="rc-roll" title={`${share}% complete`}>
            <b>{node.rollup.complete}</b>/{node.rollup.total}
            <span className="rm-meter"><i style={{ width: `${share}%` }} /></span>
          </span>
        ) : null}
        {children ? (
          <button
            type="button"
            className={cn('rc-fold nodrag', open && 'on')}
            aria-expanded={open}
            aria-label={`${open ? 'Fold' : 'Unfold'} ${node.title}`}
            onClick={(event: MouseEvent) => {
              event.stopPropagation();
              toggle(node.slug);
            }}
          >
            {open ? null : <span>{`+${node.below}`}</span>}
            <ChevronDown size={12} aria-hidden="true" />
          </button>
        ) : null}
      </div>
    </div>
  );
});

const NODE_TYPES = { rail: Card };

/* A tidy left-to-right tree: leaves take consecutive rows, a parent sits
   midway between its first and last shown child, depth sets the column. */
function layout(
  roots: string[],
  kids: Map<string, MapNode[]>,
  keep: (slug: string) => boolean,
  isOpen: (slug: string) => boolean,
): Map<string, { x: number; y: number }> {
  const placed = new Map<string, { x: number; y: number }>();
  let cursor = 0;
  const place = (slug: string, depth: number): number => {
    placed.set(slug, { x: depth * COL, y: 0 });
    const children = isOpen(slug)
      ? (kids.get(slug) ?? []).filter((child) => keep(child.slug) && !placed.has(child.slug))
      : [];
    let y: number;
    if (!children.length) {
      y = cursor * ROW;
      cursor += 1;
    } else {
      const ys = children.map((child) => place(child.slug, depth + 1));
      y = (ys[0] + ys[ys.length - 1]) / 2;
    }
    placed.set(slug, { x: depth * COL, y });
    return y;
  };
  for (const root of roots) {
    if (!keep(root) || placed.has(root)) continue;
    place(root, 0);
    cursor += ROOT_GAP;
  }
  return placed;
}

function Canvas({
  nodes,
  roots,
  kids,
  opened,
  visible,
  hits,
  changed,
  needs,
  selected,
  theme,
  canMove,
  select,
  toggle,
  propose,
}: MapCanvasProps) {
  const flow = useReactFlow<CardNode, Edge>();
  const [links, setLinks] = useState(true);
  const fromCanvas = useRef(false);
  const target = useRef('');

  const bySlug = useMemo(() => new Map(nodes.map((node) => [node.slug, node])), [nodes]);
  const keep = useCallback((slug: string) => !visible || visible.has(slug), [visible]);
  const isOpen = useCallback((slug: string) => !!visible || opened.has(slug), [visible, opened]);

  const positions = useMemo(() => layout(roots, kids, keep, isOpen), [roots, kids, keep, isOpen]);

  const laidOut = useMemo<CardNode[]>(
    () =>
      [...positions].map(([slug, position]) => {
        const node = bySlug.get(slug)!;
        const children = (kids.get(slug) ?? []).filter((child) => keep(child.slug)).length;
        return {
          id: slug,
          type: 'rail',
          position,
          selected: slug === selected,
          draggable: canMove,
          className: `cat-${node.category}`,
          data: {
            node,
            markers: changed.has(slug) ? [...node.markers, 'changed'] : node.markers,
            children,
            open: children > 0 && isOpen(slug),
            hit: hits.has(slug),
            toggle,
          },
        };
      }),
    [positions, bySlug, kids, keep, isOpen, selected, canMove, changed, hits, toggle],
  );

  const [shown, setShown, onNodesChange] = useNodesState<CardNode>(laidOut);
  useEffect(() => setShown(laidOut), [laidOut, setShown]);

  const edges = useMemo<Edge[]>(() => {
    const out: Edge[] = [];
    for (const slug of positions.keys()) {
      const node = bySlug.get(slug)!;
      if (node.parent && positions.has(node.parent)) {
        const lit = node.markers.includes('path') || node.markers.includes('focus');
        out.push({
          id: `p:${node.parent}>${slug}`,
          source: node.parent,
          target: slug,
          sourceHandle: 'out',
          targetHandle: 'in',
          type: 'smoothstep',
          className: cn('rc-edge', lit && 'lit'),
          focusable: false,
        });
      }
      if (!links) continue;
      for (const parent of node.also) {
        if (!positions.has(parent)) continue;
        out.push({
          id: `a:${parent}>${slug}`,
          source: parent,
          target: slug,
          sourceHandle: 'out',
          targetHandle: 'in',
          className: 'rc-edge also',
          focusable: false,
        });
      }
      for (const dependency of needs.get(slug) ?? []) {
        if (!positions.has(dependency)) continue;
        const done = bySlug.get(dependency)?.category === 'done';
        out.push({
          id: `d:${slug}>${dependency}`,
          source: dependency,
          target: slug,
          sourceHandle: 'dep-out',
          targetHandle: 'dep-in',
          className: cn('rc-edge dep', !done && 'waiting'),
          markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
          label: done ? undefined : 'needs',
          focusable: false,
        });
      }
    }
    return out;
  }, [positions, bySlug, needs, links]);

  const subtree = useCallback(
    (slug: string): Set<string> => {
      const found = new Set<string>([slug]);
      const stack = [slug];
      while (stack.length) {
        for (const child of kids.get(stack.pop()!) ?? []) {
          if (found.has(child.slug)) continue;
          found.add(child.slug);
          stack.push(child.slug);
        }
      }
      return found;
    },
    [kids],
  );

  const dropTarget = useCallback(
    (dragged: CardNode): string => {
      const own = subtree(dragged.id);
      const current = bySlug.get(dragged.id)?.parent;
      const over = flow
        .getIntersectingNodes(dragged)
        .map((node) => node.id)
        .find((id) => !own.has(id));
      return over && over !== current ? over : '';
    },
    [flow, subtree, bySlug],
  );

  const mark = useCallback(
    (slug: string) => {
      if (target.current === slug) return;
      target.current = slug;
      setShown((current) =>
        current.map((node) => ({
          ...node,
          className: cn(`cat-${node.data.node.category}`, node.id === slug && 'drop-target'),
        })),
      );
    },
    [setShown],
  );

  /* Find and an outside selection pan the camera; a click on the canvas
     never moves what the person just clicked. */
  useEffect(() => {
    if (!hits.size) return;
    const ids = [...hits].filter((slug) => positions.has(slug)).map((id) => ({ id }));
    if (ids.length) void flow.fitView({ nodes: ids, padding: 0.3, maxZoom: 1.1, duration: 350 });
  }, [hits, positions, flow]);

  useEffect(() => {
    if (fromCanvas.current) {
      fromCanvas.current = false;
      return;
    }
    const at = selected ? positions.get(selected) : undefined;
    if (at) void flow.setCenter(at.x + WIDTH / 2, at.y + 36, { zoom: Math.max(flow.getZoom(), 0.8), duration: 350 });
  }, [selected, positions, flow]);

  const focus = nodes.find((node) => node.markers.includes('focus'));

  return (
    <div className="rc-canvas">
      <ReactFlow<CardNode, Edge>
        nodes={shown}
        edges={edges}
        nodeTypes={NODE_TYPES}
        onNodesChange={onNodesChange}
        colorMode={theme}
        fitView
        fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
        minZoom={0.15}
        maxZoom={1.75}
        nodesConnectable={false}
        elementsSelectable
        selectNodesOnDrag={false}
        attributionPosition="top-right"
        onNodeClick={(_, node) => {
          fromCanvas.current = true;
          select(node.id);
        }}
        onNodeDoubleClick={(_, node) => {
          if ((kids.get(node.id) ?? []).length) toggle(node.id);
        }}
        onPaneClick={() => {
          if (selected) select(selected);
        }}
        onNodeDrag={(_, node) => mark(dropTarget(node))}
        onNodeDragStop={(_, node) => {
          const dst = dropTarget(node);
          mark('');
          setShown(laidOut);
          if (dst) propose(node.id, dst);
        }}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} />
        <Controls showInteractive={false} />
        <MiniMap
          pannable
          zoomable
          nodeClassName={(node) => `rc-mini ${String(node.className ?? '')}`}
          nodeBorderRadius={6}
        />
        <Panel position="top-left" className="rc-tools">
          <button type="button" className="rf-chip" onClick={() => void flow.fitView({ padding: 0.15, duration: 350 })}>
            Fit all
          </button>
          {focus && positions.has(focus.slug) ? (
            <button
              type="button"
              className="rf-chip"
              onClick={() => void flow.fitView({ nodes: [{ id: focus.slug }, ...(kids.get(focus.slug) ?? []).map((child) => ({ id: child.slug }))], padding: 0.35, maxZoom: 1.1, duration: 350 })}
            >
              <Target size={13} aria-hidden="true" /> Focus
            </button>
          ) : null}
          <button type="button" className={cn('rf-chip', links && 'on')} aria-pressed={links} onClick={() => setLinks((value) => !value)}>
            <GitBranch size={13} aria-hidden="true" /> Dependencies
          </button>
          {canMove ? <span className="rc-hint">Drag a card onto a new parent to move its branch.</span> : null}
        </Panel>
      </ReactFlow>
    </div>
  );
}
