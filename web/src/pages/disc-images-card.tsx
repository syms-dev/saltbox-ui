import { Disc3, Search, Trash2 } from 'lucide-react';

import { useDiscImages, useReplaceDiscImages } from '../api/queries';
import type { DiscImage } from '../api/types';
import { useConfirm, useToast } from '../components/feedback';
import { formatBytes } from '../format';

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

function FilmList({ films }: { films: DiscImage[] }): React.ReactNode {
  return (
    <ul className="small">
      {films.map((film) => (
        <li key={film.file_id}>
          <strong>
            {film.title}
            {film.year === null ? '' : ` (${film.year})`}
          </strong>{' '}
          <span className="muted">
            · {formatBytes(film.size)} · {film.profile}
            {film.replaceable ? '' : ' (still takes DVD quality: left alone)'}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** One Radarr's disc images, with the two ways to have them replaced. */
function Instance({
  blocked,
  films,
  name,
  size,
}: {
  blocked: string[];
  films: DiscImage[];
  name: string;
  size: number;
}): React.ReactNode {
  const replace = useReplaceDiscImages();
  const confirm = useConfirm();
  const toast = useToast();
  const replaceable = films.filter((film) => film.replaceable);

  async function run(remove: boolean): Promise<void> {
    const ok = await confirm({
      body: remove ? (
        <>
          <p>
            Radarr deletes {plural(replaceable.length, 'disc image', 'disc images')} (
            {formatBytes(replaceable.reduce((sum, film) => sum + film.size, 0))}) and searches for a proper copy of
            each. Until one is found, those films have no file.
          </p>
          <p>If Radarr has a recycle bin set (Settings, Media Management), the images go there first.</p>
        </>
      ) : (
        <p>
          Radarr searches for a proper copy of {plural(replaceable.length, 'film', 'films')}. Each disc image stays
          until a better copy is downloaded and imported, which replaces it.
        </p>
      ),
      confirmLabel: remove ? 'Delete and search' : 'Search',
      title: remove ? `Delete ${name}’s disc images?` : `Find proper copies in ${name}?`,
      tone: remove ? 'danger' : 'primary',
    });
    if (!ok) return;
    replace.mutate(
      { delete: remove, instance: name },
      {
        onError: (error) => toast({ body: error.message, title: 'Radarr did not take it', tone: 'danger' }),
        onSuccess: (result) =>
          toast({
            body: `${result.deleted > 0 ? `Deleted ${result.deleted}. ` : ''}Radarr is searching for ${plural(
              result.searched,
              'film',
              'films',
            )}; see its Activity for what it finds.`,
            title: 'Search started',
            tone: 'ok',
          }),
      },
    );
  }

  return (
    <div className="stack-sm">
      <p>
        <strong>{name}</strong> has {plural(films.length, 'film', 'films')} as disc images ({formatBytes(size)}). Plex
        and Bazarr cannot read them, and Radarr counts them as DVD quality.
      </p>
      <FilmList films={films} />
      {blocked.length === 0 ? null : (
        <p className="small muted">
          Films on {blocked.join(', ')} are left alone: that profile still allows DVD quality, so a search could bring
          another disc image. Move them to a synced profile first (Quality profiles, above).
        </p>
      )}
      {replaceable.length === 0 ? null : (
        <div className="row">
          <button className="small primary" disabled={replace.isPending} onClick={() => void run(false)} type="button">
            <Search aria-hidden="true" size={14} /> Find proper copies
          </button>
          <button className="small ghost" disabled={replace.isPending} onClick={() => void run(true)} type="button">
            <Trash2 aria-hidden="true" size={14} /> Delete the images and search
          </button>
        </div>
      )}
    </div>
  );
}

/** Films kept as whole-disc images (.iso, .img), and getting Radarr to replace them. */
export function DiscImagesCard(): React.ReactNode {
  const images = useDiscImages();
  const instances = images.data?.instances ?? [];
  const withFilms = instances.filter((i) => 'films' in i && i.films.length > 0);
  if (images.data === undefined || (withFilms.length === 0 && instances.every((i) => 'films' in i))) {
    return null; // nothing to say: no Radarr, or no disc images
  }
  return (
    <section aria-labelledby="disc-images-title" className="card stack-sm">
      <h2 className="row" id="disc-images-title">
        <Disc3 aria-hidden="true" size={18} /> Disc images in your films
      </h2>
      {instances.map((instance) =>
        'error' in instance ? (
          <p className="muted small" key={instance.name}>
            Could not read {instance.name}: {instance.error}
          </p>
        ) : instance.films.length === 0 ? null : (
          <Instance
            blocked={instance.blocked_by_profile}
            films={instance.films}
            key={instance.name}
            name={instance.name}
            size={instance.size}
          />
        ),
      )}
    </section>
  );
}
