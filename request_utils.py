from datetime import datetime, timedelta
import requests
import time
import json
import atexit
from pathlib import Path


URL = 'https://graphql.anilist.co'
MAX_PAGE_SIZE = 50  # The anilist API's max page size
API_MAX_REQ_PER_MIN = 90


def safe_post_request(post_json, oauth_token=None, verbose=True):
    """Send a post request to the AniList API, automatically waiting and retrying if the rate limit was encountered.
    Returns the 'data' field of the response. Note that this may be None if the request found nothing (404).
    """
    response = requests.post(URL, json=post_json, headers={'Authorization': oauth_token})

    # Handle rate limit
    while response.status_code == 429:
        if 'Retry-After' in response.headers:
            retry_after = int(response.headers['Retry-After']) + 1
            if verbose:
                retry_msg = f"Rate limit encountered; waiting {retry_after} seconds..."
                print(retry_msg, end='', flush=True)  # No trailing newline so we can overwrite this printout

            time.sleep(retry_after)

            # Write back over the rate limit message with whitespace
            if verbose:
                print('\r' + len(retry_msg) * " ", end='\r', flush=True)  # Both '\r' here so cursor looks nice...
        else:  # Retry-After should always be present, but have seen it be missing for some users; retry quickly
            time.sleep(0.1)
            #print(f"AniList API gave rate limit response without retry time; trying waiting {retry_after} seconds...")

        response = requests.post(URL, json=post_json, headers={'Authorization': oauth_token})

    safe_post_request.total_queries += 1  # We'll ignore requests that got 429'd

    # Handle case where response isn't valid JSON.
    try:
        response_json = response.json()
    except:
        print(f"While sending JSON request:\n{post_json}\n\nGot unparsable response:\n{response}\n\n")
        raise

    if not response.ok:
        if 'errors' in response_json:
            for error in response_json['errors']:
                print(f"Error {error['status']}: {error['message']}\n")

            raise Exception(f"Error {response_json['errors'][0]['status']}: {response_json['errors'][0]['message']}")

        response.raise_for_status()  # Raise generic error based on status code if we had no specific error messages.

    return response_json['data']


safe_post_request.total_queries = 0  # Spooky property-on-function


# Note that the anilist API's lastPage field of PageInfo is currently broken and doesn't return reliable results
def depaginated_request(query, variables, max_count=None, oauth_token=None, verbose=True):
    """Given a paginated query string, request every page and return a list of all the requested objects.

    Query must return only a single Page or paginated object subfield, and will be automatically unwrapped. page and
    perPage fields will also be automatically added to query vars.
    """
    paginated_variables = {
        **variables,
        'perPage': MAX_PAGE_SIZE
    }

    out_list = []

    page_num = 1  # Note that pages are 1-indexed
    while True:
        paginated_variables['page'] = page_num
        response_data = safe_post_request({'query': query, 'variables': paginated_variables},
                                          oauth_token=oauth_token, verbose=verbose)

        # Blindly unwrap the returned json until we see pageInfo. This unwraps both Page objects and cases where we're
        # querying a paginated subfield of some other object.
        # E.g. if querying Media.staff.edges, unwraps "Media" and "staff" to get {"pageInfo":... "edges"...}
        while 'pageInfo' not in response_data:
            assert response_data, "Could not find pageInfo in paginated request."
            assert len(response_data) == 1, "Cannot de-paginate query with multiple returned fields."

            response_data = response_data[next(iter(response_data))]  # Unwrap

        # Grab the non-PageInfo query result
        assert len(response_data) == 2, "Cannot de-paginate query with multiple returned fields."
        out_list.extend(next(v for k, v in response_data.items() if k != 'pageInfo'))

        if max_count is not None and len(out_list) >= max_count:
            return out_list[:max_count]

        if not response_data['pageInfo']['hasNextPage']:
            return out_list

        page_num += 1


def dict_intersection(dicts):
    """Given an iterable of dicts, return a list of the intersection of their keys, while preserving the order of the
    keys from the first given dict.
    """
    dicts = list(dicts)  # Avoid gotchas if we were given an iterator
    if not dicts:
        return []

    return [k for k in dicts[0] if all(k in d for d in dicts[1:])]


def dict_diffs(dicts):
    """Given an iterable of dicts, return an equal-length list of lists containing each dict's keys unique to it,
    preserving each dict's original key order.
    """
    dicts = list(dicts)  # Avoid gotchas if we were given an iterator

    result = []
    for cur_dict in dicts:
        unique_keys = set(cur_dict).difference(*(d.keys() for d in dicts if d is not cur_dict))
        result.append([k for k in cur_dict if k in unique_keys])  # Return keys in the dict's original order.

    return result


def cache(file_name, max_age: timedelta):
    """Memoize the given JSON-serializable function result and cache to the given JSON file on program exit, expiring
    cache values after a given datetime.timedelta.
    """
    cache = json.load(open(file_name, 'r')) if Path(file_name).is_file() else {}
    Path(file_name).parent.mkdir(exist_ok=True)  # Create the cache dir as needed
    atexit.register(lambda: json.dump(cache, open(file_name, 'w')))

    def decorator(func):
        def new_func(*args, **kwargs):
            param = str([args, kwargs])  # Squash multiple args together
            if param not in cache or datetime.fromisoformat(cache[param][1]) < datetime.now():
                cache[param] = [func(*args, **kwargs), (datetime.now() + max_age).isoformat()]
            elif cache[param][0] and isinstance(cache[param][0], dict) and list(cache[param][0].keys())[0].isdigit():
                # Dumb quick hack fix for ID-dict keys getting int->str mangled by JSON-serialization.
                # Doing this properly requires arbitrary object traversal which is hard or pickle which is insecure.
                return {int(k): v for k, v in cache[param][0].items()}

            return cache[param][0]
        return new_func

    return decorator
