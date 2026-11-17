import { Check, ChevronRight, RefreshCw, RotateCcw, Sparkles, Stethoscope, Wrench } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { Link, useLocation } from 'react-router-dom';

import {
  useDismiss,
  useExplain,
  useExplainStatus,
  useFixProblem,
  useProblems,
  useRefreshProblems,
} from '../api/queries';
import type { ExplainStatus, Explanation, LogGroup, LogProblems } from '../api/types';
import { useConfirm, useToast } from '../components/feedback';
import { formatRelative } from '../format';
import type { Attention } from './home-attention';
import styles from './problems-card.module.css';

type Finding = { app: string; group: LogGroup; logs: string; name: string };

/** Every recurring error, with the app it belongs to and the way to that app's log. */
export function findings(problems: LogProblems | undefined): Finding[] {
  return (problems?.containers ?? []).flatMap((container) =>
    container.groups.map((group) => ({
      app: container.app?.title ?? container.name,
      group,
      logs: container.app === null ? '/apps' : `/apps/${container.app.repo}/${container.app.role}?tab=logs`,
      name: container.name,
    })),
  );
}

function times(count: number): string {
  return count === 1 ? 'once' : `${count} times`;
}

/** Shown as an open issue: not dismissed, not marked fixed (unless it came back), not gone quiet. */
export function isOpen(group: LogGroup): boolean {
  return group.dismissed !== true && group.fixed !== true && group.quiet !== true;
}

/** Known errors that mean something is not working belong on Home, while they are open. */
export function problemAttention(problems: LogProblems | undefined): Attention[] {
  return findings(problems)
    .filter((finding) => finding.group.rule?.severity === 'problem' && isOpen(finding.group))
    .map((finding) => ({
      actions: [{ kind: 'link' as const, label: 'How to fix it', to: '/health#problems' }],
      detail: `${finding.app} logged it ${times(finding.group.count)} in the last day.`,
      id: finding.group.key,
      title: finding.group.rule?.title ?? finding.app,
      tone: 'warn' as const,
    }));
}

type Kind = 'minor' | 'notice' | 'problem' | 'unexplained';

const KINDS: Record<Kind, string> = {
  minor: 'Minor',
  notice: 'Harmless',
  problem: 'Needs fixing',
  unexplained: 'Unexplained',
};

function kindOf(group: LogGroup): Kind {
  return group.rule?.severity ?? 'unexplained';
}

/** A step with `commands` set in backticks shown as code. */
function Step({ text }: { text: string }): React.ReactNode {
  return (
    <li>
      {text.split('`').map((part, index) =>
        // Parts between a pair of backticks are the odd ones; the sentence is fixed, so its
        // position is a stable key.
        index % 2 === 1 ? <code key={index}>{part}</code> : <span key={index}>{part}</span>,
      )}
    </li>
  );
}

const CONFIDENCE: Record<Explanation['confidence'], string> = {
  high: 'fairly sure',
  low: 'not sure',
  medium: 'reasonably sure',
};

/** What Claude said about an issue: marked as its reading, with how sure it is. */
function ExplanationBlock({ explanation }: { explanation: Explanation }): React.ReactNode {
  return (
    <div className={styles['explanation']}>
      <h4 className={styles['label']}>
        <Sparkles aria-hidden="true" size={12} /> Claude’s explanation · {CONFIDENCE[explanation.confidence]}
      </h4>
      <p>{explanation.summary}</p>
      {explanation.cause === '' ? null : <p>{explanation.cause}</p>}
      {explanation.steps.length === 0 ? null : (
        <ol className={styles['steps']}>
          {explanation.steps.map((step) => (
            <Step key={step} text={step} />
          ))}
        </ol>
      )}
      <p className="small muted">
        Written by Claude from {explanation.lines_sent} log lines
        {(explanation.docs ?? []).length > 0 ? (
          <>
            {' '}
            and Saltbox’s docs for{' '}
            {(explanation.docs ?? []).map((doc, index) => (
              <span key={doc.url}>
                {index > 0 ? ', ' : ''}
                <a href={doc.url} rel="noreferrer" target="_blank">
                  {doc.title}
                </a>
              </span>
            ))}
          </>
        ) : null}
        , {formatRelative(explanation.asked_at)}. It ran nothing on the server and it can be wrong: check before you act
        on it.
      </p>
    </div>
  );
}

/** One issue: what it is, whose it is, what to do, and the buttons that act on it. */
function Issue({ explainer, finding }: { explainer: ExplainStatus | undefined; finding: Finding }): React.ReactNode {
  const { group } = finding;
  const kind = kindOf(group);
  const dismiss = useDismiss();
  const fix = useFixProblem();
  const explain = useExplain();
  const confirm = useConfirm();
  const toast = useToast();
  const explanation = group.explanation ?? null;

  async function applyFix(): Promise<void> {
    if (action === null) return;
    const ok = await confirm({
      body: (
        <>
          <p>{action.detail}</p>
          <p>The file names come from {finding.app}’s log. Check they are what you expect before going on.</p>
        </>
      ),
      confirmLabel: action.label,
      title: `${action.label}?`,
    });
    if (!ok) return;
    fix.mutate(group.key, {
      onError: (error) => toast({ body: error.message, title: 'The fix did not work', tone: 'danger' }),
      onSuccess: (result) => toast({ body: `Created ${result.created.join(', ')}.`, title: 'Fixed', tone: 'ok' }),
    });
  }

  async function ask(): Promise<void> {
    const ok = await confirm({
      body: (
        <>
          <p>
            This sends the error and about 25 lines of {finding.app}’s log around it to Anthropic, through Claude Code
            signed in as <code>{explainer?.user}</code> on this server, with Saltbox’s own documentation for the apps
            involved (a copy comes with this app). Passwords and keys this app knows about are masked first; file names,
            addresses and whatever else the log says are sent as they are.
          </p>
          <p>Claude only explains. It cannot read or change anything on the server, and nothing it says is run.</p>
        </>
      ),
      confirmLabel: 'Ask Claude',
      title: `Ask Claude about this ${finding.app} error?`,
    });
    if (!ok) return;
    explain.mutate(
      { again: explanation !== null, key: group.key },
      { onError: (error) => toast({ body: error.message, title: 'Claude could not explain it', tone: 'danger' }) },
    );
  }
  const title = group.rule?.title ?? `${finding.app} keeps logging the same error`;
  const subjects = group.subjects ?? [];
  const action = group.action ?? null;
  return (
    <article aria-label={title} className={`${styles['issue']} ${styles[kind]}`}>
      <header className={styles['head']}>
        <span className={`${styles['badge']} ${styles[`${kind}Badge`]}`}>{KINDS[kind]}</span>
        {group.returned === true ? (
          <span className={`${styles['badge']} ${styles['problemBadge']}`}>Came back after you marked it fixed</span>
        ) : null}
        <span className="small muted">
          {finding.app} · logged {times(group.count)}
          {group.last_seen === '' ? '' : `, last ${formatRelative(group.last_seen)}`}
        </span>
      </header>
      <h3 className={styles['flush']}>{title}</h3>
      {group.rule === null ? (
        <p>
          There is no explanation on file for this one. The line below is its latest occurrence; the rest of{' '}
          {finding.app}’s log around it usually says what it was doing.
        </p>
      ) : (
        <>
          <p>{group.rule.meaning}</p>
          <div>
            <h4 className={styles['label']}>What to do</h4>
            <ol className={styles['steps']}>
              {group.rule.fix.map((step) => (
                <Step key={step} text={step} />
              ))}
            </ol>
          </div>
        </>
      )}
      {subjects.length === 0 ? null : (
        <div>
          <h4 className={styles['label']}>
            {subjects.length + (group.more_subjects ?? 0) === 1 ? 'The file' : 'The files'}
          </h4>
          <ul className={styles['files']}>
            {subjects.map((subject) => (
              <li key={subject}>{subject}</li>
            ))}
            {(group.more_subjects ?? 0) > 0 ? <li>and {group.more_subjects} more</li> : null}
          </ul>
        </div>
      )}
      <div>
        <h4 className={styles['label']}>Latest line</h4>
        <p className={styles['line']}>{group.message}</p>
      </div>
      {explanation === null ? null : <ExplanationBlock explanation={explanation} />}
      {action === null ? null : <p className="small muted">{action.detail}</p>}
      <footer className={styles['actions']}>
        {action === null ? null : (
          <button className="small primary" disabled={fix.isPending} onClick={() => void applyFix()} type="button">
            <Wrench aria-hidden="true" size={14} /> {action.label}
          </button>
        )}
        <Link className="button small" to={finding.logs}>
          Open {finding.app}’s log <ChevronRight aria-hidden="true" size={14} />
        </Link>
        {explainer?.signed_in === true ? (
          <button className="small" disabled={explain.isPending} onClick={() => void ask()} type="button">
            <Sparkles aria-hidden="true" className={explain.isPending ? 'spin' : ''} size={14} />{' '}
            {explain.isPending ? 'Claude is reading…' : explanation === null ? 'Ask Claude' : 'Ask Claude again'}
          </button>
        ) : null}
        <span className="spacer" />
        <button
          className="small"
          disabled={dismiss.isPending}
          onClick={() => dismiss.mutate({ dismiss: true, key: group.key, kind: 'fixed', label: title })}
          title="Hide it now. It comes back if the app logs it again."
          type="button"
        >
          <Check aria-hidden="true" size={14} /> Mark fixed
        </button>
        <button
          className="small ghost"
          disabled={dismiss.isPending}
          onClick={() => dismiss.mutate({ dismiss: true, key: group.key, label: title })}
          title="Hide it for good, even if it keeps happening."
          type="button"
        >
          Dismiss
        </button>
      </footer>
    </article>
  );
}

function titleOf(finding: Finding): string {
  return finding.group.rule?.title ?? `${finding.app} keeps logging the same error`;
}

/** Why an issue is out of the way, in a few words. */
function setAside(group: LogGroup): string {
  if (group.fixed === true && group.fixed_at != null) return `marked fixed ${formatRelative(group.fixed_at)}`;
  if (group.dismissed === true) return 'dismissed';
  const last = group.last_seen_at ?? (group.last_seen === '' ? null : group.last_seen);
  return last === null ? 'not seen lately' : `last seen ${formatRelative(last)}`;
}

/** Issues out of the way: quiet, marked fixed, or dismissed, each with a way back. */
function SetAside({ items, summary }: { items: Finding[]; summary: string }): React.ReactNode {
  const dismiss = useDismiss();
  return (
    <details className={styles['dismissed']}>
      <summary>{summary}</summary>
      <ul className={styles['dismissedList']}>
        {items.map((finding) => {
          const { group } = finding;
          const title = titleOf(finding);
          const marked = group.dismissed === true || group.fixed === true;
          return (
            <li key={group.key}>
              <span className={styles['dismissedText']}>
                <strong>{title}</strong>
                <span className="small muted">
                  {finding.app} · {setAside(group)}
                </span>
              </span>
              {marked ? (
                <button
                  className="small ghost"
                  disabled={dismiss.isPending}
                  onClick={() => dismiss.mutate({ dismiss: false, key: group.key, label: title })}
                  type="button"
                >
                  <RotateCcw aria-hidden="true" size={14} /> Show again
                </button>
              ) : (
                <button
                  className="small ghost"
                  disabled={dismiss.isPending}
                  onClick={() => dismiss.mutate({ dismiss: true, key: group.key, kind: 'fixed', label: title })}
                  type="button"
                >
                  <Check aria-hidden="true" size={14} /> Mark fixed
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </details>
  );
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** How to turn explanations on, shown while they are off. */
function ExplainSetup({ status }: { status: ExplainStatus }): React.ReactNode {
  return (
    <details className={styles['dismissed']}>
      <summary>Have Claude explain an issue</summary>
      <p className="small">
        Each issue gets an “Ask Claude” button once{' '}
        <a href="https://code.claude.com/docs/en/setup" rel="noreferrer" target="_blank">
          Claude Code
        </a>{' '}
        is installed on this server and signed in for <code>{status.user}</code>
        {status.installed ? ' (it is installed, but not signed in)' : ''}. Sign in over SSH as that user with{' '}
        <code>claude auth login</code>; it uses your Claude plan, or an API key if that is how you sign in. This app
        never sees the sign-in. Claude is sent one error and the log around it when you ask, and can only answer.
      </p>
    </details>
  );
}

const ORDER: Kind[] = ['problem', 'minor', 'unexplained', 'notice'];

/** Errors that keep coming back in the apps' logs, one card each; the known ones say what to do. */
export function ProblemsCard(): React.ReactNode {
  const problems = useProblems();
  const refresh = useRefreshProblems();
  const explainer = useExplainStatus();
  const location = useLocation();
  const section = useRef<HTMLElement>(null);
  const loaded = problems.data !== undefined;
  useEffect(() => {
    // Home links here by name; bring the card into view once it has something to show.
    if (loaded && location.hash === '#problems') section.current?.scrollIntoView({ block: 'start' });
  }, [loaded, location.hash]);

  const all = findings(problems.data);
  const open = all
    .filter((finding) => isOpen(finding.group))
    .sort(
      (a, b) =>
        Number(b.group.returned === true) - Number(a.group.returned === true) ||
        ORDER.indexOf(kindOf(a.group)) - ORDER.indexOf(kindOf(b.group)),
    );
  const marked = all.filter((finding) => finding.group.dismissed === true || finding.group.fixed === true);
  const quiet = all.filter((finding) => !isOpen(finding.group) && !marked.includes(finding));
  const quietHours = Math.round((problems.data?.quiet_after ?? 21_600) / 3600);
  const reading = problems.isPending || refresh.isPending;
  return (
    <section aria-labelledby="problems-title" className="card stack-sm" id="problems" ref={section}>
      <div className="row">
        <h2 className={styles['title']} id="problems-title">
          <Stethoscope aria-hidden="true" size={18} /> Problems in app logs
        </h2>
        <span className="spacer" />
        <button className="small ghost" disabled={reading} onClick={() => refresh.mutate()} type="button">
          <RefreshCw aria-hidden="true" className={reading ? 'spin' : ''} size={14} /> Check again
        </button>
      </div>
      {problems.data === undefined ? (
        <p className="muted small">
          {problems.isError
            ? `Could not read the logs: ${problems.error.message}`
            : 'Reading the last day of every app’s log…'}
        </p>
      ) : !problems.data.available ? (
        <p className="muted small">Docker is not answering, so the logs cannot be read.</p>
      ) : (
        <>
          <p className="muted small">
            {open.length === 0
              ? `No error keeps coming back in the last day of ${problems.data.scanned} containers’ logs`
              : `${open.length} issue${open.length === 1 ? '' : 's'} in the last day of ${problems.data.scanned} containers’ logs`}
            , checked {formatRelative(problems.data.checked_at)}.
          </p>
          <div className={styles['issues']}>
            {open.map((finding) => (
              <Issue explainer={explainer.data} finding={finding} key={finding.group.key} />
            ))}
          </div>
          {quiet.length === 0 ? null : (
            <SetAside
              items={quiet}
              summary={`${plural(quiet.length, 'issue', 'issues')} not seen for ${quietHours} hours or more, probably dealt with`}
            />
          )}
          {marked.length === 0 ? null : (
            <SetAside
              items={marked}
              summary={`${plural(marked.length, 'issue', 'issues')} marked fixed or dismissed`}
            />
          )}
          {explainer.data !== undefined && !explainer.data.signed_in && open.length > 0 ? (
            <ExplainSetup status={explainer.data} />
          ) : null}
        </>
      )}
    </section>
  );
}
