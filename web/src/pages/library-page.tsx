import { DiscImagesCard } from './disc-images-card';
import { RecyclarrCard } from './recyclarr-card';
import { SeedingCard } from './seeding-card';
import { PageHead } from './server-pages';

/** How the media library is handled: what quality is fetched, disc images to replace, and seeding. */
export function LibraryPage(): React.ReactNode {
  return (
    <div className="stack">
      <PageHead title="Library">
        How your shows and films are handled: the quality Sonarr and Radarr look for, films that arrived as disc images,
        and how long finished downloads keep seeding.
      </PageHead>
      <RecyclarrCard />
      <DiscImagesCard />
      <SeedingCard />
    </div>
  );
}
