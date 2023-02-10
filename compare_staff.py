import argparse
import asyncio
from collections import Counter

import staff_types
from utils import safe_post_request, depaginated_request, dict_intersection

STAFF_COL_WIDTH = 20
SHOW_COL_WIDTH = 40
COL_SEP = 3
NUM_SHOWS_ALL_STAFF = 5  # How many shows to list for most total shared staff
NUM_SHOWS_SUB_STAFF = 3  # How many shows to list for most of each sub-category of staff


# Ideally we could sort on [SEARCH_MATCH, POPULARITY_DESC], but this doesn't seem to work as expected in the case of
# shows with the exact same title (e.g. Golden Time); the less popular one is still returned.
# TODO: Grab multiple in one query, and if the string match is exact return the most popular?
async def get_show(search, sort_by="SEARCH_MATCH"):
    """Given an approximate show name, return the closest-matching show with ID and title.
    Default sorts by closeness of the string match. Use e.g. POPULARITY_DESC for cases where shows share a name (e.g.
    "Golden Time" will by default return the one no one cares about).
    """
    query = '''
query ($search: String, $sort: MediaSort) {
    Media(search: $search, type: ANIME, sort: [$sort]) {
        id
        title {
            english
            romaji
        }
    }
}'''
    result = await safe_post_request({'query': query, 'variables': {'search': search, 'sort': sort_by}})
    if result is not None:
        result = result['Media']

        # In case a show has no english title, fall back to romaji
        title = result['title']['english'] if result['title']['english'] is not None else result['title']['romaji']
        assert title is not None, f"API returned an untitled show for \"{search}\" (show ID: {result['id']})"

        result = {'id': result['id'], 'title': title}

    return result


async def get_show_studios(show_id):
    """Given a show ID, return a dict of its studios, formatted as id: {"name": "...", "roles": ["..."]}."""
    query = '''
query ($mediaId: Int) {
    Media(id: $mediaId) {
        studios {
            edges {
                node {
                    id
                    name
                }
                isMain
            }
        }
    }
}'''
    # Since the API doesn't sort by isMain, handle main vs supporting studios separately, so we can return main
    # studio(s) at the front of the results
    main_studios_dict = {}
    supporting_studios_dict = {}

    # the Media.studios API also does not seem to be paginated even though StudioConnection has pageInfo
    for edge in (await safe_post_request({'query': query, 'variables': {'mediaId': show_id}}))['Media']['studios']['edges']:
        if edge['isMain']:
            main_studios_dict[edge['node']['id']] = {'name': edge['node']['name'], 'roles': ["Main"]}
        else:
            supporting_studios_dict[edge['node']['id']] = {'name': edge['node']['name'], 'roles': ["Supporting"]}

    return main_studios_dict | supporting_studios_dict


async def get_show_production_staff(show_id):
    """Given a show ID, return a dict of its production staff, formatted as id: {"name": "...", "roles": ["..."]}."""
    query = '''
query ($mediaId: Int, $page: Int, $perPage: Int) {
    Media(id: $mediaId) {
        staff(sort: RELEVANCE, page: $page, perPage: $perPage) {
            pageInfo {
                hasNextPage
            }
            # Direct `nodes` field is also available, but it includes duplicates per edge (e.g. one staff with two roles
            # shows up twice even though nodes don't include role), so avoiding it to keep things intuitive.
            edges {
                node {
                    id
                    name {
                        full
                    }
                }
                role
            }
        }
    }
}'''
    staff_dict = {}

    for edge in await depaginated_request(query=query, variables={'mediaId': show_id}):
        # Account for staff potentially having multiple roles
        if edge['node']['id'] not in staff_dict:
            staff_dict[edge['node']['id']] = {'name': edge['node']['name']['full'],
                                              'roles': []}

        staff_dict[edge['node']['id']]['roles'].append(edge['role'])

    return staff_dict


async def get_show_voice_actors(show_id, language="JAPANESE"):
    """Given a show ID, return a dict of its voice actors for the given language (default: "JAPANESE"), formatted as:
    id: {"name": "...", "roles": ["MAIN: Edward Elric", "SUPPORTING: Edward Elric (child)"]}.
    """
    query = '''
query ($mediaId: Int, $language: StaffLanguage, $page: Int, $perPage: Int) {
    Media(id: $mediaId) {
        characters(sort: [ROLE, RELEVANCE], page: $page, perPage: $perPage) {
            pageInfo {
                hasNextPage
            }
            edges {
                node {  # Character
                    name {
                        full
                    }
                }
                role  # MAIN, SUPPORTING, or BACKGROUND
                voiceActorRoles(language: $language) {  # This is a list, but the API doesn't make us paginate it
                    voiceActor {
                        id
                        name {
                            full
                        }
                    }
                    roleNotes
                }
            }
        }
    }
}'''
    vas_dict = {}

    for edge in await depaginated_request(query=query, variables={'mediaId': show_id, 'language': language}):
        for va_role in edge['voiceActorRoles']:
            # Account for VAs potentially having multiple roles
            if va_role['voiceActor']['id'] not in vas_dict:
                vas_dict[va_role['voiceActor']['id']] = {'name': va_role['voiceActor']['name']['full'],
                                                         'roles': []}

            role_descr = edge['role'] + " " + edge['node']['name']['full']
            if va_role['roleNotes'] is not None:
                role_descr += " " + va_role['roleNotes']

            vas_dict[va_role['voiceActor']['id']]['roles'].append(role_descr)

    return vas_dict


async def get_production_staff_shows(staff_id):
    """Given a staff id, return a dict of shows they've been a production staff member for and the corresponding roles.
    Formatted as {show_id: {'title': "...",
                            'roles': ["role1", "role2"]}}
    """
    query = '''
query ($staffId: Int, $page: Int, $perPage: Int) {
    Staff(id: $staffId) {
        staffMedia(type: ANIME, sort: POPULARITY_DESC, page: $page, perPage: $perPage) {
            pageInfo {
                hasNextPage
            }
            edges {
                node {  # Show
                    id
                    title {  # Include title to save a query
                        english
                        romaji
                    }
                }
                staffRole
            }
        }
    }
}'''
    shows_dict = {}

    for edge in await depaginated_request(query=query, variables={'staffId': staff_id}):
        show = edge['node']
        # Account for staff potentially having multiple roles in a show
        if show['id'] not in shows_dict:
            # In case a show has no english title, fall back to romaji
            title = show['title']['english'] if show['title']['english'] is not None else show['title']['romaji']
            shows_dict[edge['node']['id']] = {'title': title,
                                              'roles': []}

        shows_dict[show['id']]['roles'].append(edge['staffRole'])

    return shows_dict


async def get_related_shows(show_id):
    """Given a show ID, return a set of IDs for all shows that are directly or indirectly related to it."""
    query = '''
query ($mediaId: Int) {
    Media(id: $mediaId) {
        relations {  # Has pageInfo but doesn't accept page args
            edges {
                relationType
                node {  # Show
                    id
                    title {
                        english
                        romaji
                    }
                    type
                    format
                    tags {
                        name  # Grabbed so we can more effectively ignore crossovers
                    }
                }
            }
        }
    }
}'''
    # TODO: Ignore 'CHARACTER' relation type?
    queue = {show_id}
    related_show_ids = {show_id}  # Including itself to start avoids special-casing
    while queue:
        cur_show_id = queue.pop()
        relations = (await safe_post_request({'query': query,
                                              'variables': {'mediaId': cur_show_id}}))['Media']['relations']['edges']
        for relation in relations:
            # Manga don't need to be included in the output and ignoring them trims our search queries way down
            if relation['node']['id'] not in related_show_ids and relation['node']['type'] == 'ANIME':
                related_show_ids.add(relation['node']['id'])

                # Don't add things to the queue that are likely to explode our search (but add them to the ignore list)
                if (relation['relationType'] in {'OTHER'}
                        or relation['node']['format'] in {'MUSIC'}  # TODO: Probably don't need this anymore
                        or any(tag['name'] == 'Crossover' for tag in relation['node']['tags'])):
                    continue

                queue.add(relation['node']['id'])

    related_show_ids.remove(show_id)
    return related_show_ids


async def main(args=None):
    parser = argparse.ArgumentParser(
        description="Find all studios/staff/VAs common to all of the given shows.\n"
                    "If given only one show, list shows with highest numbers of shared staff and compare to the top"
                    " match.",
        formatter_class=argparse.RawTextHelpFormatter)  # Preserves newlines in help text
    parser.add_argument('show_names', nargs='+', metavar='shows', help="Show(s) to compare.")
    parser.add_argument('-t', '--top', type=int, default=5,
                        help="How many top matching shows to list when given only one show. Default 5.")
    parser.add_argument('-p', '--popularity', action='store_true',
                        help="Match more popular shows instead of the closest string matches to the given show names.\n"
                             "Helpful in cases like e.g. Golden Time where another show of the same name exists.")
    parser.add_argument('--ignore-related', action='store_true',
                        help="Ignore directly or indirectly related shows (sequels, prequels, OVAs, etc.) when\n"
                             "searching for similar shows")
    args = parser.parse_args(args)

    # Lookup each show by name and collect studios/staff/VAs data from them
    shows = []
    for show_name in args.show_names:
        # Get the exact show ID and title based on the given approximate name
        show = await get_show(show_name, sort_by='POPULARITY_DESC' if args.popularity else 'SEARCH_MATCH')
        if show is None:
            raise ValueError(f"Could not find show matching {show}")

        # Add data on studios, production staff, and vas
        show['studios'] = await get_show_studios(show['id'])
        show['production_staff'] = await get_show_production_staff(show['id'])
        show['voice_actors'] = await get_show_voice_actors(show['id'], language="JAPANESE")
        shows.append(show)

    # If given only one show, find the show with the most shared production staff and compare it
    # TODO: Also find anime by similarity of animation staff vs script/directors vs music vs VAs
    if len(shows) == 1:
        show = shows[0]
        if len(show['production_staff']) > 70:
            print(f"Searching for other shows worked on by staff of `{show['title']}`, this may take a couple minutes...")

        # Ignore the show itself when searching, and related shows if specified
        ignored_show_ids = {show['id']}
        if args.ignore_related:
            ignored_show_ids.update(await get_related_shows(show['id']))
            if len(ignored_show_ids) > 1:
                print(f"Ignoring {len(ignored_show_ids) - 1} related show(s)\n")

        # Query each staff member for the IDs of all anime they've had production roles in and keep a tally
        # TODO: This takes prohibitively many queries. We can be more clever and exit slightly early once a show is in
        #       the lead by >= num_remaining_staff (or if we list top N, when the Nth is that far ahead of (N + 1)th).
        #       After exiting early if we want the exact staff counts we can query the top shows directly for their
        #       staff, which takes far fewer queries.
        #       We can also query the top N shows directly fairly early so we know their true counts and the cutoff
        #       point will be detected sooner, fully querying any show that enters the top N. However this gets
        #       complicated by multiple tracked categories so may not be worth it (categories like 'writing' tend to
        #       have only 1-2 overlaps per show at most so won't be able to early exit).
        show_counts = Counter()
        # Keep additional sub-tallies for shows with overlapping staff of a specific type
        music_show_counts = Counter()
        visuals_show_counts = Counter()
        writing_show_counts = Counter()

        # Keep a dict of show IDs -> titles we encounter along the way for convenience
        ids_to_titles = {}
        for staff_id, staff_info in show['production_staff'].items():
            roles = staff_info['roles']
            # Find all shows this staff member has had production roles in
            show_roles = await get_production_staff_shows(staff_id)  # dict of show_id: {title: "...", roles: [...]}
            ids_to_titles.update((k, v['title']) for k, v in show_roles.items())  # Track titles for future ref

            show_counts.update(show_id for show_id in show_roles.keys() if show_id not in ignored_show_ids)

            # For each class of role, tally shows where the staff member has previously had the same class of role
            trimmed_roles = [staff_types.trim_role(role) for role in roles]
            for role, trimmed_role in zip(roles, trimmed_roles):
                if trimmed_role not in staff_types.all_:
                    print(f"Ignoring unknown role {role}")

            for type_counter, roles_of_type in [[music_show_counts, staff_types.music],
                                                [visuals_show_counts, staff_types.visuals],
                                                [writing_show_counts, staff_types.writing]]:
                if any(role in roles_of_type for role in trimmed_roles):
                    # This is expensive but will typically only be hit once per staff
                    type_counter.update(show_id for show_id, v in show_roles.items()
                                        if (show_id not in ignored_show_ids
                                            and any(staff_types.trim_role(r) in roles_of_type for r in v['roles'])))

        if not show_counts:
            print(f"Staff for {show['title']} have not done any other shows.")
            exit()

        # Report the top 5 matching shows by total production staff
        # Make sure to ignore the given show as it will always have the most matches. However check for its ID instead
        # of blindly skipping the top match, just in case of ties (e.g. an unreleased show with very few staff listed
        # might be completely supersetted).
        top_shows = show_counts.most_common(args.top)
        # Add the top show by total production staff for comparison
        other_show_id = top_shows[0][0]
        shows.append({'id': other_show_id,
                      'title': ids_to_titles[other_show_id],
                      'studios': await get_show_studios(other_show_id),
                      'production_staff': await get_show_production_staff(other_show_id),
                      'voice_actors': await get_show_voice_actors(other_show_id, language="JAPANESE")})

        print(f"Shows with most production staff in common with {show['title']}:")
        for other_show_id, shared_staff_count in top_shows:
            print(f"    {shared_staff_count:2} | {ids_to_titles[other_show_id][:2 * SHOW_COL_WIDTH]}")
        print("")

        # Report the top 3 matching shows for each subcategory
        top_shows = [item for item in music_show_counts.most_common(NUM_SHOWS_SUB_STAFF) if item[0] != show['id']]
        if top_shows:  # Skip if no matches
            print(f"Show with most music staff in common with {show['title']}:")
            for other_show_id, shared_staff_count in top_shows:
                print(f"    {shared_staff_count:2} | {ids_to_titles[other_show_id][:2 * SHOW_COL_WIDTH]}")
            print("")

        top_shows = [item for item in visuals_show_counts.most_common(NUM_SHOWS_SUB_STAFF) if item[0] != show['id']]
        if top_shows:  # Skip if no matches
            print(f"Show with most art/animation staff in common with {show['title']}:")
            for other_show_id, shared_staff_count in top_shows:
                print(f"    {shared_staff_count:2} | {ids_to_titles[other_show_id][:2 * SHOW_COL_WIDTH]}")
            print("")

        top_shows = [item for item in writing_show_counts.most_common(NUM_SHOWS_SUB_STAFF) if item[0] != show['id']]
        if top_shows:  # Skip if no matches
            print(f"Show with most writing staff in common with {show['title']}:")
            for other_show_id, shared_staff_count in top_shows:
                print(f"    {shared_staff_count:2} | {ids_to_titles[other_show_id][:2 * SHOW_COL_WIDTH]}")
            print("")

        # TODO: Report the top show by VAs

        print("")

    col_widths = [STAFF_COL_WIDTH] + [SHOW_COL_WIDTH] * len(shows)
    total_width = sum(col_widths) + COL_SEP * (len(col_widths) - 1)  # Adjust for separator

    def col_print(items):
        """Print the given strings left-justified in the appropriate width columns, truncating them if too long."""
        print((COL_SEP * ' ').join(item[:col_width].ljust(col_width) for item, col_width in zip(items, col_widths)))

    col_print([""] + [show['title'] for show in shows])

    # List common studios/staff, sectioned separately by studios vs production staff vs voice actors
    common_found = False
    for staff_type, show_staff_dicts in [["Studios", [show['studios'] for show in shows]],
                                         ["Production Staff", [show['production_staff'] for show in shows]],
                                         ["Voice Actors (JP)", [show['voice_actors'] for show in shows]]]:
        # Find the common staff between the shows. Use a helper to avoid sets so that dict ordering is maintained
        common_staff_ids = dict_intersection(show_staff_dicts)

        if common_staff_ids:
            if common_found:  # Quick hack to avoid leading newlines
                print("\n")
            common_found = True

            print(staff_type)
            print("═" * total_width)

            for staff_id in common_staff_ids:
                # Print a row(s) with the staff name followed by their role(s) in each show
                max_roles = max(len(show_staff[staff_id]['roles']) for show_staff in show_staff_dicts)
                for i in range(max_roles):
                    cols = [show_staff_dicts[0][staff_id]['name'] if i == 0 else ""]
                    cols.extend((show_staff[staff_id]['roles'][i] if i < len(show_staff[staff_id]['roles']) else "")
                                for show_staff in show_staff_dicts)
                    col_print(cols)

    if not common_found:
        print("")
        print("No common studios/staff/VAs found!".center(total_width))

    print(f"\nTotal queries: {safe_post_request.total_queries}")
    safe_post_request.total_queries = 0


if __name__ == '__main__':
    asyncio.run(main())
