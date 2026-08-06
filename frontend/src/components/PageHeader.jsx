export function PageHeader({ kicker, title, titleId, description, aside, className = "page-heading" }) {
  return (
    <header className={className}>
      <div>
        {kicker && <p className="page-kicker">{kicker}</p>}
        <h1 id={titleId}>{title}</h1>
        {description && <p className="page-description">{description}</p>}
      </div>
      {aside && <div className="page-heading-aside">{aside}</div>}
    </header>
  );
}
