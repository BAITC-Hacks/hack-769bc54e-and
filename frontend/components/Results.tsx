"use client";

import ReactMarkdown from "react-markdown";
import { DATE_COMPARISON, HONESTY_TEXT, OUTCOME, REJECT_REASONS, RESULTS } from "@/lib/brand";
import {
  capitalize, explanations, factsLine, findSearchResult, kzt, outcomeKind, parseItems, quoteText,
  reasonCounts, suggestions,
} from "@/lib/results";
import type { ContractorCard, Run, SearchResult } from "@/lib/types";

function HonestyBadges({ card }: { card: ContractorCard }) {
  return (
    <div className="honesty-badges" aria-label={HONESTY_TEXT.sectionLabel}>
      <span className={`honesty-badge ${card.flags.synthetic ? "honesty-badge-synthetic" : "honesty-badge-source"}`}>
        {card.flags.synthetic ? HONESTY_TEXT.syntheticProfile : HONESTY_TEXT.sourceProfile}
      </span>
      {card.flags.price_imputed && <span className="honesty-badge honesty-badge-estimate">{HONESTY_TEXT.imputedPrice}</span>}
      {card.flags.city_imputed && <span className="honesty-badge honesty-badge-estimate">{HONESTY_TEXT.imputedCity}</span>}
    </div>
  );
}

function Card({
  card,
  text,
  writing,
  hideIdentity,
  different,
}: {
  card: ContractorCard;
  text: string | null;
  writing: boolean;
  hideIdentity: boolean;
  different: boolean;
}) {
  return (
    <li className={`card${hideIdentity ? " card-identity-hidden" : ""}${different ? " card-different" : ""}`}>
      <div className="card-head">
        <h3 className="card-name">{hideIdentity ? RESULTS.hiddenCardLabel : card.name}</h3>
        {!hideIdentity && (
          <span className="card-price">
            {card.price_from_kzt === null ? RESULTS.priceUnknown : `${RESULTS.pricePrefix} ${kzt(card.price_from_kzt)}`}
          </span>
        )}
      </div>
      {!hideIdentity && <p className="card-meta">{card.categories.join(" · ")} · {card.city}</p>}
      <HonestyBadges card={card} />
      {different && <span className="difference-badge">{DATE_COMPARISON.onlyOnThisDate}</span>}
      {text ? (
        <div className="card-why">
          <ReactMarkdown>{text}</ReactMarkdown>
        </div>
      ) : writing ? (
        <p className="card-why card-pending">{RESULTS.writing}</p>
      ) : (
        <div className="card-why">
          <p>{factsLine(card)}</p>
          <p className="card-note">{RESULTS.factsNote}</p>
        </div>
      )}
      {card.match.quote && (
        <figure className="card-quote">
          <figcaption>{RESULTS.quoteLabel}</figcaption>
          <blockquote>«{quoteText(card.match.quote)}»</blockquote>
        </figure>
      )}
    </li>
  );
}

/** Плашка исхода: метка, заголовок, цифры. По ней исход различим до чтения карточек. */
function Outcome({ found }: { found: SearchResult }) {
  const kind = outcomeKind(found);
  const shown = found.cards.length;
  const passed = found.passed_filters ?? 0;
  const total = found.in_city_and_category ?? 0;
  const noCategory = found.outcome === "no_category_in_city";

  const title =
    kind === "full" ? OUTCOME.full.title(shown, passed)
    : kind === "partial" ? OUTCOME.partial.title(shown)
    : kind === "none" ? OUTCOME.none.title(total)
    : noCategory && found.note ? capitalize(found.note)
    : OUTCOME.absent.unknownTitle;
  const sub =
    kind === "full" || kind === "partial" ? OUTCOME.passed(passed, total)
    : kind === "none" || noCategory ? null
    : found.note ?? null;

  return (
    <div className={`outcome outcome-${kind}`}>
      <span className="outcome-badge">{OUTCOME[kind].badge}</span>
      <p className="outcome-title">{title}</p>
      {sub && <p className="outcome-sub">{sub}</p>}
      {found.warning && <p className="outcome-warning">{capitalize(found.warning)}</p>}
    </div>
  );
}

/**
 * Почему меньше трёх и что изменить. Всё — из данных инструмента дословно, без модели:
 * пустой результат обязан быть объяснён словами и без ключей (R10, R18).
 */
function Diagnosis({ found }: { found: SearchResult }) {
  const reasons = reasonCounts(found);
  const tips = suggestions(found);
  const season = found.diagnosis?.season_note;
  if (!reasons.length && !tips.length && !season) return null;

  return (
    <div className="diagnosis">
      {reasons.length > 0 && (
        <section>
          <h3>{RESULTS.whyTitle}</h3>
          <ul className="reasons">
            {reasons.map(([code, count]) => (
              <li key={code}>
                <strong>{count}</strong> {REJECT_REASONS[code] ?? code}
              </li>
            ))}
          </ul>
          {reasons.length > 1 && <p className="card-note">{RESULTS.whyHint}</p>}
        </section>
      )}
      {(tips.length > 0 || season) && (
        <section>
          <h3>{RESULTS.changeTitle}</h3>
          {tips.length > 0 && (
            <ul className="tips">
              {tips.map((tip) => (
                <li key={tip}>{capitalize(tip)}</li>
              ))}
            </ul>
          )}
          {season && <p className="season">{capitalize(season)}</p>}
        </section>
      )}
    </div>
  );
}

/**
 * Итог подбора: исход, карточки из search_contractors в его порядке, объяснения — из ответа
 * модели, диагностика — из инструмента. Пустого экрана нет ни в одном состоянии запуска.
 * В mock-режиме ответ модели — сырой JSON, поэтому его не разбираем.
 */
export function Results({
  run,
  mock,
  identitiesHidden,
  onToggleIdentities,
  title = RESULTS.title,
  titleId = "results-title",
  showIdentityToggle = true,
  differingIds = new Set<string>(),
}: {
  run: Run;
  mock: boolean;
  identitiesHidden: boolean;
  onToggleIdentities: () => void;
  title?: string;
  titleId?: string;
  showIdentityToggle?: boolean;
  differingIds?: ReadonlySet<string>;
}) {
  const found = findSearchResult(run);
  if (!found) {
    if (run.status === "running")
      return (
        <section className="results">
          <div className="outcome outcome-pending">
            <p className="outcome-title">{RESULTS.searching}</p>
          </div>
        </section>
      );
    if (run.status === "failed")
      return (
        <section className="results">
          <div className="outcome outcome-none">
            <p className="outcome-title">{RESULTS.failedTitle}</p>
            <p className="outcome-sub">{RESULTS.failedHint}</p>
          </div>
        </section>
      );
    // Поиск не состоялся (запрос не разобран, модель не вызвала инструмент) — ответ модели как есть
    if (!run.final_report) return null;
    return (
      <div className="report results">
        <ReactMarkdown>{run.final_report}</ReactMarkdown>
      </div>
    );
  }

  const texts = explanations(found.cards, mock || !run.final_report ? [] : parseItems(run.final_report));
  const writing = run.status === "running";

  return (
    <section className="results" aria-labelledby={titleId}>
      <div className="results-head">
        <h2 id={titleId}>{title}</h2>
        {showIdentityToggle && found.cards.length > 0 && (
          <button className="btn-link" type="button" aria-pressed={identitiesHidden} onClick={onToggleIdentities}>
            {identitiesHidden ? RESULTS.showIdentities : RESULTS.hideIdentities}
          </button>
        )}
      </div>
      <Outcome found={found} />
      {found.cards.length > 0 && (
        <ol className="cards">
          {found.cards.map((card, i) => (
            <Card
              key={card.id}
              card={card}
              text={texts[i]}
              writing={writing}
              hideIdentity={identitiesHidden}
              different={differingIds.has(card.id)}
            />
          ))}
        </ol>
      )}
      {outcomeKind(found) !== "full" && <Diagnosis found={found} />}
    </section>
  );
}
