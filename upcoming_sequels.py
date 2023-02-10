"""Given an anilist username, check what shows from their completed or planning lists have known upcoming seasons."""

import argparse
import asyncio
from datetime import datetime

from utils import safe_post_request, depaginated_request, async_any


async def get_user_id_by_name(username):
    """Given an AniList username, fetch the user's ID."""
    query_user_id = '''
query ($username: String) {
    User (name: $username) {
        id
    }
}'''

    return (await safe_post_request({'query': query_user_id, 'variables': {'username': username}}))['User']['id']


async def get_user_media(user_id, status='COMPLETED'):
    """Given an AniList user ID, fetch their anime list, returning a list of media objects sorted by score (desc)."""
    query = '''
query ($userId: Int, $status: MediaListStatus, $page: Int, $perPage: Int) {
    Page (page: $page, perPage: $perPage) {
        pageInfo {
            hasNextPage
        }
        # Note that a MediaList object is actually a single list entry, hence the need for pagination
        # IMPORTANT: Always include MEDIA_ID in the sort, as the anilist API is bugged - if ties are possible,
        #            pagination can omit some results while duplicating others at the page borders.
        mediaList(userId: $userId, status: $status, sort: [SCORE_DESC, MEDIA_ID]) {
            media {
                id
                title {
                    english
                    romaji
                }
            }
        }
    }
}'''

    return [list_entry['media'] for list_entry in await depaginated_request(query=query,
                                                                            variables={'userId': user_id, 'status': status})]


async def get_season_shows(season: str, season_year: int) -> list:
    """Given a season (WINTER, SPRING, SUMMER, FALL) and year, return a list of shows from that season."""
    query = '''
query ($season: MediaSeason, $seasonYear: Int, $page: Int, $perPage: Int) {
    Page (page: $page, perPage: $perPage) {
        pageInfo {
            hasNextPage
        }
        media(season: $season, seasonYear: $seasonYear, type: ANIME, format_in: [TV, MOVIE], sort: POPULARITY_DESC) {
            id
            title {
                english
                romaji
            }
        }
    }
}'''
    return await depaginated_request(query=query, variables={'season': season, 'seasonYear': season_year})


def fuzzy_date_greater_or_equal_to(fuzzy_date, date: datetime):
    """Given a FuzzyDate as returned by anilist, return True if the date *could* be higher than the given date."""
    if fuzzy_date['day'] is not None:
        return datetime(year=fuzzy_date['year'],
                        month=fuzzy_date['month'],
                        day=fuzzy_date['day']) >= date
    elif fuzzy_date['month'] is not None:
        return fuzzy_date['year'] > date.year or (fuzzy_date['year'] == date.year and fuzzy_date['month'] >= date.month)
    elif fuzzy_date['year'] is not None:
        return fuzzy_date['year'] >= date.year

    return True


async def get_related_media(show_id):
    """Given a media ID, return a generator of IDs for all airing or future anime that are direct or indirect relations of it.

    Also return their airing season and relation type.

    Optionally provide a set of media IDs to ignore (e.g. also going to be searched) to cut query count.
    """
    query = '''
query ($mediaId: Int) {
    Media(id: $mediaId) {
        relations {  # Has pageInfo but doesn't accept page args
            edges {
                relationType
                node {  # Media
                    id
                    title {
                        english
                        romaji
                    }
                    type
                    format
                    tags {
                        name  # Grabbed so we can ignore crossovers to help avoid exploding the search
                    }
                }
            }
        }
    }
}'''
    queue = {show_id}
    related_show_ids = {show_id}  # Including itself to start avoids special-casing
    while queue:
        cur_show_id = queue.pop()
        relations = (await safe_post_request({'query': query,
                                             'variables': {'mediaId': cur_show_id}}))['Media']['relations']['edges']
        for relation in relations:
            show = relation['node']
            # Manga don't need to be included in the output and ignoring them trims our search queries way down
            if show['id'] not in related_show_ids:
                related_show_ids.add(show['id'])
                if show['id'] != show_id:
                    yield show

                # Only chain through a few relation types to keep the search small
                if (relation['relationType'] not in {'SEQUEL', 'PREQUEL', 'SOURCE', 'ALTERNATIVE'}
                        or any(tag['name'] == 'Crossover' for tag in show['tags'])):
                    continue

                queue.add(show['id'])


async def main(args=None):
    """Main entrypoint with optional args."""
    parser = argparse.ArgumentParser(
        description="Given an anilist username, check what shows from their completed or planning lists have known\n"
                    "upcoming seasons.",
        formatter_class=argparse.RawTextHelpFormatter)  # Preserves newlines in help text
    parser.add_argument('username', help="User whose list should be checked.")
    parser.add_argument('-p', '--planning', action='store_true',
                        help="Check only for sequels of shows in the user's planning list.")
    parser.add_argument('-c', '--completed', action='store_true',
                        help="Check only for sequels of shows in the user's completed list.")
    args = parser.parse_args(args)

    user_id = await get_user_id_by_name(args.username)

    # Fetch the user's relevant media lists (anime or manga)
    user_media_ids_by_status = {status: set(media['id'] for media in await get_user_media(user_id, status))
                                for status in ('COMPLETED', 'PLANNING', 'CURRENT')}
    user_media_ids = set().union(*user_media_ids_by_status.values())

    # Search four seasons, including the current season unless it's in its last month
    cur_date = datetime.utcnow()
    for i in range(4):
        season_idx = cur_date.month // 3 + i  # cur month is 1-indexed, so we're looking ahead a month as desired
        season = ['WINTER', 'SPRING', 'SUMMER', 'FALL'][season_idx % 4]
        year = cur_date.year + season_idx // 4

        if i != 0:
            print("")
        print(f"{season} {year}")
        print("=" * 40)

        # Search each of the seasonal shows' relations for a show in the user's list
        for show in await get_season_shows(season=season, season_year=year):
            # `any` doesn't work with async generators so rolled our own
            if await async_any(related_media['id'] in user_media_ids async for related_media in get_related_media(show['id'])):
                print(show['title']['english'] or show['title']['romaji'])

    print(f"\nTotal queries: {safe_post_request.total_queries}")
    safe_post_request.total_queries = 0


if __name__ == '__main__':
    asyncio.run(main())
