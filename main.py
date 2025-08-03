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
		print("Using cached followers")
		with open(cache_file, 'r', encoding='utf-8') as f:
			return json.load(f)
	else:
		print("Fetching followers")
		followers = await gather(api.followers(user_id))
		follower_dicts = [follower.dict() for follower in followers]
		with open(cache_file, 'w', encoding='utf-8') as f:
			# noinspection PyTypeChecker
			json.dump(follower_dicts, f, indent=4, default=lambda o: o.isoformat() if isinstance(o, dt) else o)
		return follower_dicts


async def get_following(api, user_id, cache_file):
	if os.path.exists(cache_file):
		print("Using cached following")
		with open(cache_file, 'r', encoding='utf-8') as f:
			return json.load(f)
	else:
		print("Fetching following")
		following = await gather(api.following(user_id))
		following_dicts = [following.dict() for following in following]
		with open(cache_file, 'w', encoding='utf-8') as f:
			# noinspection PyTypeChecker
			json.dump(following_dicts, f, indent=4, default=lambda o: o.isoformat() if isinstance(o, dt) else o)
		return following_dicts


async def get_follow_all(followers, following, target_user, cache_file, include_self=True):
	if os.path.exists(cache_file):
		print("Using cached follow-all")
		with open(cache_file, 'r', encoding='utf-8') as f:
			return json.load(f)
	else:
		print("Creating follow-all union")
		users = followers + following
		if include_self:
			print(f"Including target user: @{target_user.username}")
			users.append(target_user.dict())
		follow_all_dict = {user['id']: user for user in users}
		follow_all = list(follow_all_dict.values())
		with open(cache_file, 'w', encoding='utf-8') as f:
			# noinspection PyTypeChecker
			json.dump(follow_all, f, indent=4, default=lambda o: o.isoformat() if isinstance(o, dt) else o)
		return follow_all


async def main(target_username, include_self=True):
	os.makedirs('input', exist_ok=True)
	os.makedirs('output', exist_ok=True)
	os.makedirs('output/users', exist_ok=True)

	api = API(pool='input/accounts.db')

	await api.pool.add_account("unused", "unused", "unused@example.com", "unused", cookies=get_cookies())
	await api.pool.login_all()

	print(f"Target user: @{target_username}")
	user = await api.user_by_login(target_username)

	cache_file_followers = f"output/{target_username}-followers.json"
	followers = await get_followers(api, user.id, cache_file_followers)
	print(f"Got {len(followers)} followers")

	cache_file_following = f"output/{target_username}-following.json"
	following = await get_following(api, user.id, cache_file_following)
	print(f"Got {len(following)} following")

	cache_file_follow_all = f"output/{target_username}-follow-all.json"
	follow_all = await get_follow_all(followers, following, user, cache_file_follow_all, include_self)
	print(f"Got {len(follow_all)} unique users in follow-all")


if __name__ == "__main__":
	asyncio.run(main(target_username="drxwilhelm", include_self=True))
