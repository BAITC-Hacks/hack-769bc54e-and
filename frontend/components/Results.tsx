"use client";

import ReactMarkdown from "react-markdown";
import { HONESTY_TEXT, RESULTS } from "@/lib/brand";
import { EMPTY_REPORT, explanations, factsLine, findSearchResult, kzt, parseReport, quoteText } from "@/lib/results";
import type { ContractorCard, Run } from "@/lib/types";

function HonestyBadges({ card }: { card: ContractorCard }) {
  return (
    <div className="honesty-badges" aria-label={HONESTY_TEXT.sectionLabel}>
      <span className={`honesty-badge ${card.flags.synthetic ? "honesty-badge-team" : "honesty-badge-source"}`}>
        {card.flags.synthetic ? HONESTY_TEXT.syntheticProfile : HONESTY_TEXT.sourceProfile}
      </span>
      {card.flags.price_imputed && <span className="honesty-badge honesty-badge-estimate">{HONESTY_TEXT.imputedPrice}</span>}
      {card.flags.city_imputed && <span className="honesty-badge honesty-badge-estimate">{HONESTY_TEXT.imputedCity}</span>}
    </div>
  );
}

function Card({ card, text, writing }: { card: ContractorCard; text: string | null; writing: boolean }) {
  return (
    <li className="card">
      <div className="card-head">
        <h3 className="card-name">{card.name}</h3>
        <span className="card-price">
          {card.price_from_kzt === null ? RESULTS.priceUnknown : `${RESULTS.pricePrefix} ${kzt(card.price_from_kzt)}`}
        </span>
      </div>
      <p className="card-meta">
        {card.categories.join(" · ")} · {card.city}
      </p>
      <HonestyBadges card={card} />
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

/**
 * Итог подбора: карточки из search_contractors в его порядке, объяснения — из ответа модели.
 * В mock-режиме ответ модели — сырой JSON, поэтому его не разбираем.
 */
export function Results({ run, mock }: { run: Run; mock: boolean }) {
  const found = findSearchResult(run);
  if (!found) {
    // Поиск не состоялся (ошибка, запрос не разобран) — показываем ответ модели как есть
    if (run.status !== "done" || !run.final_report) return null;
    return (
      <div className="report results">
        <ReactMarkdown>{run.final_report}</ReactMarkdown>
      </div>
    );
  }

  const report = mock || !run.final_report ? EMPTY_REPORT : parseReport(run.final_report);
  const texts = explanations(found.cards, report.items);
  const writing = run.status === "running";

  return (
    <section className="results" aria-labelledby="results-title">
      <h2 id="results-title">{RESULTS.title}</h2>
      <p className="results-count">
        {found.outcome === "no_category_in_city" && found.note
          ? found.note
          : RESULTS.count(found.cards.length, found.passed_filters ?? 0, found.in_city_and_category)}
      </p>
      {report.intro && (
        <div className="report results-text">
          <ReactMarkdown>{report.intro}</ReactMarkdown>
        </div>
      )}
      {found.cards.length > 0 && (
        <ol className="cards">
          {found.cards.map((card, i) => (
            <Card key={card.id} card={card} text={texts[i]} writing={writing} />
          ))}
        </ol>
      )}
      {report.outro && (
        <div className="report results-text">
          <ReactMarkdown>{report.outro}</ReactMarkdown>
        </div>
      )}
    </section>
  );
}
