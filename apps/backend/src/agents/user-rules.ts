import { existsSync, readdirSync, readFileSync } from 'fs';
import { join } from 'path';

import { env } from '../env';

/**
 * Reads user-defined rules from RULES.md in the project folder if it exists
 */
export function getUserRules(): string | null {
	const projectFolder = env.NAO_DEFAULT_PROJECT_PATH;

	if (!projectFolder) {
		return null;
	}

	const rulesPath = join(projectFolder, 'RULES.md');

	if (!existsSync(rulesPath)) {
		return null;
	}

	try {
		const rulesContent = readFileSync(rulesPath, 'utf-8');
		return rulesContent;
	} catch (error) {
		console.error('Error reading RULES.md:', error);
		return null;
	}
}

type Repository = {
	name: string;
	hasDbtProject: boolean;
	dbtProjectPath?: string;
	indexed?: boolean;
};

export function getRepositories(): Repository[] | null {
	const projectFolder = env.NAO_DEFAULT_PROJECT_PATH;

	if (!projectFolder) {
		return null;
	}

	const reposPath = join(projectFolder, 'repos');

	if (!existsSync(reposPath)) {
		return null;
	}

	try {
		const entries = readdirSync(reposPath, { withFileTypes: true });
		const repositories: Repository[] = [];

		for (const entry of entries) {
			if (!entry.isDirectory()) {
				continue;
			}

			const rootDbtProject = join(reposPath, entry.name, 'dbt_project.yml');
			const subDbtProject = join(reposPath, entry.name, 'dbt', 'dbt_project.yml');

			let hasDbtProject = false;
			let dbtProjectPath: string | undefined;

			if (existsSync(rootDbtProject)) {
				hasDbtProject = true;
				dbtProjectPath = `repos/${entry.name}`;
			} else if (existsSync(subDbtProject)) {
				hasDbtProject = true;
				dbtProjectPath = `repos/${entry.name}/dbt`;
			}

			let indexed = false;
			if (hasDbtProject) {
				const indexPath = join(reposPath, '..', 'dbt-index', entry.name, 'manifest.md');
				indexed = existsSync(indexPath);
			}

			repositories.push({ name: entry.name, hasDbtProject, dbtProjectPath, indexed });
		}

		return repositories.length > 0 ? repositories : null;
	} catch (error) {
		console.error('Error reading repos folder:', error);
		return null;
	}
}

type Connection = {
	type: string;
	database: string;
};

export function getConnections(): Connection[] | null {
	const projectFolder = env.NAO_DEFAULT_PROJECT_PATH;

	if (!projectFolder) {
		return null;
	}

	const databasesPath = join(projectFolder, 'databases');

	if (!existsSync(databasesPath)) {
		return null;
	}

	try {
		const entries = readdirSync(databasesPath, { withFileTypes: true });
		const connections: Connection[] = [];

		for (const entry of entries) {
			if (entry.isDirectory() && entry.name.startsWith('type=')) {
				const type = entry.name.slice('type='.length);
				if (type) {
					const typePath = join(databasesPath, entry.name);
					const dbEntries = readdirSync(typePath, { withFileTypes: true });

					for (const dbEntry of dbEntries) {
						if (dbEntry.isDirectory() && dbEntry.name.startsWith('database=')) {
							const database = dbEntry.name.slice('database='.length);
							if (database) {
								connections.push({ type, database });
							}
						}
					}
				}
			}
		}

		return connections.length > 0 ? connections : null;
	} catch (error) {
		console.error('Error reading databases folder:', error);
		return null;
	}
}
