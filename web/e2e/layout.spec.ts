import { expect, sidewaysOverflow, test } from './fixtures';

const PAGES = [
  '/',
  '/apps',
  '/apps/discover',
  '/apps/saltbox/sonarr',
  '/apps/saltbox/sonarr?tab=logs',
  '/apps/saltbox/sonarr?tab=settings',
  '/updates',
  '/activity',
  '/health',
  '/storage',
  '/network',
  '/backups',
  '/library',
  '/connect',
  '/automation',
  '/config/accounts',
  '/config/inventory',
];

for (const path of PAGES) {
  test(`${path} fits the screen without scrolling sideways`, async ({ page }) => {
    await page.goto(path);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    await page.waitForLoadState('networkidle');
    expect(await sidewaysOverflow(page)).toBe(0);
  });
}

test('the current menu entry stays readable after a tap', async ({ page }) => {
  await page.goto('/');
  // The sidebar calls the apps "Installed"; the phone's bottom bar, "Apps".
  const nav = page.getByRole('navigation', { name: 'Main' }).filter({ visible: true });
  const apps = nav.getByRole('link', { name: /^(Installed|Apps)/ });
  await apps.tap();
  await expect(page.getByRole('heading', { level: 1, name: 'Apps' })).toBeVisible();
  await expect(apps).toHaveAttribute('aria-current', 'page');
  const [color, background] = await apps.evaluate((el) => {
    const style = getComputedStyle(el);
    return [style.color, style.backgroundColor];
  });
  expect(color).not.toBe(background);
});

test('search opens from the top bar', async ({ page }) => {
  await page.goto('/');
  await page
    .getByRole('button', { name: /Search/ })
    .first()
    .tap();
  await expect(page.getByRole('dialog', { name: 'Search and commands' })).toBeVisible();
});

test('on a phone, More opens every page', async ({ page }) => {
  await page.goto('/');
  const more = page.getByRole('button', { name: 'More' });
  test.skip(!(await more.isVisible()), 'the sidebar shows every page on wider screens');
  await more.tap();
  const sheet = page.getByRole('dialog', { name: 'All pages' });
  await expect(sheet.getByRole('link')).toHaveCount(13);
  await sheet.getByRole('link', { name: 'Config files' }).tap();
  await expect(page.getByRole('heading', { level: 1, name: 'Config files' })).toBeVisible();
  await expect(sheet).toBeHidden();
  // Not one of the four tabs, so More shows where you are.
  await expect(more).toHaveClass(/active/);
});
