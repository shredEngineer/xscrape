import asyncio
from twscrape import API, gather

import os
import json
from datetime import datetime as dt


def get_cookies():
	os.makedirs('input', exist_ok=True)
	with open('input/cookies.txt', 'r', encoding='utf-8') as f:
		cookies = f.read().strip()
	return cookies


async def get_followers(api, user_id, cache_file):
	if os.path.exists(cache_file):
		with open(cache_file, 'r', encoding='utf-8') as f:
			return json.load(f)
	else:
		followers = await gather(api.followers(user_id))
		follower_dicts = [follower.dict() for follower in followers]
		with open(cache_file, 'w', encoding='utf-8') as f:
			# noinspection PyTypeChecker
			json.dump(follower_dicts, f, indent=4, default=lambda o: o.isoformat() if isinstance(o, dt) else o)
		return follower_dicts


async def main(target_username):
	api = API(pool='input/accounts.db')

	await api.pool.add_account("unused", "unused", "unused@example.com", "unused", cookies=get_cookies())
	await api.pool.login_all()

	user = await api.user_by_login(target_username)
	print(f"User ID: {user.id}")

	cache_file = f"output/{target_username}-followers.json"
	followers = await get_followers(api, user.id, cache_file)
	print(f"Got {len(followers)} followers (from cache or API)")


if __name__ == "__main__":
	asyncio.run(main(target_username="drxwilhelm"))
