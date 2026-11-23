import type { ReactNode } from "react";

export function DiscoveryTopicCard({ category, title, description, selected, topAction, actions, children }: {
  category?: string;
  title: string;
  description: string;
  selected: boolean;
  topAction?: ReactNode;
  actions: ReactNode;
  children?: ReactNode;
}) {
  return <article className={`topic-card ${selected ? "selected" : ""}`}>
    {(category || topAction) && <div className="topic-card-top">
      {category && <span className="topic-category-pill">{category}</span>}
      {topAction}
    </div>}
    <h3>{title}</h3>
    <p className="topic-description">{description}</p>
    {children}
    <div className="topic-card-actions">{actions}</div>
  </article>;
}
