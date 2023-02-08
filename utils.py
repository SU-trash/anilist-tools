import requests
import time

URL = 'https://graphql.anilist.co'
MAX_PAGE_SIZE = 50  # The anilist API's max page size


def safe_post_request(post_json, verbose=True):
    """Send a post request to the AniList API, automatically waiting and retrying if the rate limit was encountered.
    Returns the 'data' field of the response. Note that this may be None if the request found nothing (404).
    """
    print(f"Call #{safe_post_request.total_queries} to safe_post_request")
    # The AniList API normally returns responses with a header: `access-control-allow-origin: *`
    # which dodges CORS headaches.
    # However the retry-after response appears not to do so, and causes our pyodide patch of the requests library
    # to complain that it's not allowed to view the response body because we're not an allowed origin anymore, or
    # something.
    # Manually catch the exception. The exception type also appears to be from the pyodide module, but pyodide only
    # runs in browsers and so can't be pip-installed, meaning we can't directly catch pyodide.JsException...
    # so do a horrible string exception type check instead
    try:
        response = requests.post(URL, json=post_json)
    except Exception as e:
        if str(type(e)) == "<class 'pyodide.JsException'>":
            print(f"Rate limit encountered; waiting 61 seconds as a guesstimate...")
            time.sleep(61)
            response = requests.post(URL, json=post_json)
        else:
            raise

    # Handle rate limit
    while response.status_code == 429:
        if 'Retry-After' in response.headers:
            retry_after = int(response.headers['Retry-After']) + 1
            if verbose:
                retry_msg = f"Rate limit encountered; waiting {retry_after} seconds..."
                print(retry_msg)
                #print(retry_msg, end='', flush=True)  # No trailing newline so we can overwrite this printout

            time.sleep(retry_after)

            # Write back over the rate limit message with whitespace
            # if verbose:
            #     print('\r' + len(retry_msg) * " ", end='\r', flush=True)  # Both '\r' here so cursor looks nice...
        else:  # Retry-After should always be present, but have seen it be missing for some users; retry quickly
            time.sleep(0.1)
            #print(f"AniList API gave rate limit response without retry time; trying waiting {retry_after} seconds...")

        try:
            response = requests.post(URL, json=post_json)
        except Exception as e:
            if str(type(e)) == "<class 'pyodide.JsException'>":
                print(f"Rate limit encountered; waiting 61 seconds as a guesstimate...")
                time.sleep(61)
                response = requests.post(URL, json=post_json)
            else:
                raise

    safe_post_request.total_queries += 1  # We'll ignore requests that got 429'd

    if not response.ok:
        if "errors" in response.json():
            print(response.json()['errors'])
        response.raise_for_status()

    return response.json()['data']


safe_post_request.total_queries = 0  # Spooky property-on-function


# Note that the anilist API's lastPage field of PageInfo is currently broken and doesn't return reliable results
def depaginated_request(query, variables, verbose=True):
    """Given a paginated query string, request every page and return a list of all the requested objects.

    Query must return only a single Page or paginated object subfield, and will be automatically unwrapped.
    """
    paginated_variables = {
        **variables,
        'perPage': MAX_PAGE_SIZE
    }

    out_list = []

    page_num = 1  # Note that pages are 1-indexed
    while True:
        paginated_variables['page'] = page_num
        response_data = safe_post_request({'query': query, 'variables': paginated_variables}, verbose=verbose)

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

        if not response_data['pageInfo']['hasNextPage']:
            return out_list

        page_num += 1


def dict_intersection(dicts):
    """Given an iterable of dicts, return a list of the intersection of their keys, while preserving the order of the
    keys from the first given dict."""

    dicts = list(dicts)  # Avoid gotchas if we were given an iterator
    if not dicts:
        return []

    return [k for k in dicts[0] if all(k in d for d in dicts[1:])]
