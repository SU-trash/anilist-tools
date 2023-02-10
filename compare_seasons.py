"""Given an anilist username, and two anime seasons, compare the user's completed/watching from each, by score."""

import argparse
import asyncio

from utils import safe_post_request, depaginated_request
from upcoming_sequels import get_user_id_by_name


# TODO: Use MediaListCollection to get 500 entries at a time instead of 50
# TODO: Proper object-oriented library with e.g. User.shows(fields=[...])
async def get_user_shows(user_id, status_in=('COMPLETED',)) -> list:
    """Given an AniList user ID, fetch the user's anime with given statuses, returning a list of show
     JSONs, including and sorted on score (desc).
     Include season and seasonYear.
     """
    query = '''
query ($userId: Int, $statusIn: [MediaListStatus], $page: Int, $perPage: Int) {
    Page (page: $page, perPage: $perPage) {
        pageInfo {
            hasNextPage
        }
        # Note that a MediaList object is actually a single list entry, hence the need for pagination
        # IMPORTANT: Always include MEDIA_ID in the sort, as the anilist API is bugged - if ties are possible,
        #            pagination can omit some results while duplicating others at the page borders.
        mediaList(userId: $userId, type: ANIME, status_in: $statusIn, sort: [SCORE_DESC, MEDIA_ID]) {
            media {
                id
                title {
                    english
                    romaji
                }
                season
                seasonYear
            }
            score
        }
    }
}'''

    return [{**list_entry['media'], 'score': list_entry['score']}  # Stuff score in too
            for list_entry in await depaginated_request(query=query, variables={'userId': user_id, 'statusIn': status_in})]


async def main(args=None):
    parser = argparse.ArgumentParser(
        description="Given an anilist username, check what shows from their completed or planning lists have known\n"
                    "upcoming seasons.",
        formatter_class=argparse.RawTextHelpFormatter)  # Preserves newlines in help text
    parser.add_argument('username', help="User whose list should be checked.")
    parser.add_argument('seasons', nargs='+',
                        help='Seasons or years to compare, formatted as e.g. 2021 or "Winter 2021".\n'
                             'Seasons are Winter, Spring, Summer, Fall.')
    args = parser.parse_args(args)

    user_id = await get_user_id_by_name(args.username)
    user_shows = await get_user_shows(user_id, status_in=('COMPLETED', 'CURRENT'))

    # Pick out the user's watching/completed anime from each season and their scores
    seasonal_user_shows = []
    for season_str in args.seasons:
        *season, year = season_str.split()  # Handle both "year" and "season year"
        year = int(year)
        if season:
            season = season[0].upper()

        # Note that the user list is already sorted by score so this will be too
        season_user_shows = [show for show in user_shows
                             if show['seasonYear'] == year and (show['season'] == season or not season)]
        seasonal_user_shows.append(season_user_shows)

    # Printout the info
    def pad(s, width):
        return str(s)[:width].ljust(width)

    print('   '.join(pad(season, 30) for season in args.seasons))
    print("=" * 28 * len(args.seasons))
    for i in range(max(len(shows) for shows in seasonal_user_shows)):
        print('   '.join(pad(shows[i]['score'], 3) + '  ' + pad(shows[i]['title']['english']
                                                                or shows[i]['title']['romaji'], 25)
                         if i < len(shows) else 30 * ' '
                         for shows in seasonal_user_shows))

    print(f"\nTotal queries: {safe_post_request.total_queries}")
    safe_post_request.total_queries = 0


if __name__ == '__main__':
    asyncio.run(main())
