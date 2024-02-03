import asyncio
import requests

URL = 'https://graphql.anilist.co'
MAX_PAGE_SIZE = 50  # The anilist API's max page size


# TODO: I'm pretty sure as of pyscript 2024.1.2 time.sleep works and I don't need the code to be async anymore.
#       Ref: https://github.com/pyscript/pyscript/issues/324#issuecomment-1843654051
async def safe_post_request(post_json, verbose=True):
    """Send a post request to the AniList API, automatically waiting and retrying if the rate limit was encountered.
    Returns the 'data' field of the response. Note that this may be None if the request found nothing (404).
    """
    response = None
    while response is None or response.status_code == 429:
        try:
            response = requests.post(URL, json=post_json)
        except Exception as e:
            # The AniList API normally returns responses with a header: `access-control-allow-origin: *`
            # which dodges CORS headaches.
            # However, the retry-after response appears not to do so and causes our pyodide patch of the requests module
            # to complain that it's not allowed to view the response body because we're not an allowed origin anymore,
            # or something.
            # Manually catch the exception. The exception type is from the pyodide module, but pyodide only runs in
            # browsers and so can't be pip-installed, meaning we can't directly catch pyodide.JsException...
            # so do a horrible string exception type check instead
            if str(type(e)) == "<class 'pyodide.JsException'>":
                # We don't have the actual response with Retry-After so guess it
                retry_msg = f"Rate limit encountered; waiting 61 seconds..."
                print(retry_msg, end='', flush=True)  # No trailing newline so we can overwrite this printout
                await asyncio.sleep(61)  # time.sleep doesn't work in pyscript
                print('\r' + len(retry_msg) * " ", end='\r', flush=True)  # Erase the rate limit message with whitespace
                continue

            raise

        # Handle rate limit responses
        if 'Retry-After' in response.headers:
            retry_after = int(response.headers['Retry-After']) + 1
            if verbose:
                retry_msg = f"Rate limit encountered; waiting {retry_after} seconds..."
                print(retry_msg, end='', flush=True)  # No trailing newline so we can overwrite this printout

            await asyncio.sleep(retry_after)

            # Write back over the rate limit message with whitespace
            if verbose:
                print('\r' + len(retry_msg) * " ", end='\r', flush=True)  # Both '\r' here so cursor looks nice...
        else:  # Retry-After should always be present, but have seen it be missing for some users; retry quickly
            await asyncio.sleep(0.1)

    safe_post_request.total_queries += 1  # We'll ignore requests that got 429'd

    if not response.ok:
        if "errors" in response.json():
            print(response.json()['errors'])
        response.raise_for_status()

    return response.json()['data']


safe_post_request.total_queries = 0  # Spooky property-on-function


# Note that the anilist API's lastPage field of PageInfo is currently broken and doesn't return reliable results
async def depaginated_request(query, variables, verbose=True):
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
        response_data = await safe_post_request({'query': query, 'variables': paginated_variables}, verbose=verbose)

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


async def async_any(bool_async_iterable):
    """Helper since built-in any() doesn't work on async generators."""
    async for thing in bool_async_iterable:
        if thing:
            return True

    return False


async def async_all(bool_async_iterable):
    async for thing in bool_async_iterable:
        if not thing:
            return False

    return True
