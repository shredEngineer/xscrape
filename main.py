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


async def get_follow_all(followers, following, target_user, cache_file, include_self):
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


async def get_user_data(api, user_dict, cache_dir, fetch_limit, replies_limit):
	os.makedirs(cache_dir, exist_ok=True)
	user_id = user_dict['id']
	cache_file = f"{cache_dir}/{user_id}-data.json"
	if os.path.exists(cache_file):
		print(f"Using cached data for @{user_dict['username']}")
		with open(cache_file, 'r', encoding='utf-8') as f:
			return json.load(f)
	else:
		print(f"Fetching tweets for @{user_dict['username']}")
		try:
			tweets = await gather(api.user_tweets(user_id, limit=fetch_limit))
			tweets_dicts = [tweet.dict() for tweet in tweets]
			total_replies = 0
			for t in tweets_dicts:
				t.pop('media', None)
				t.pop('user', None)
				if replies_limit == -1 and t.get('replyCount', 0) > 0:
					t['replies'] = [reply.dict() for reply in await gather(api.tweet_replies(t['id']))]
				elif replies_limit > 0 and t.get('replyCount', 0) > 0:
					t['replies'] = [reply.dict() for reply in await gather(api.tweet_replies(t['id'], limit=replies_limit))]
				else:
					t['replies'] = []
				for r in t.get('replies', []):
					r.pop('media', None)
					r.pop('user', None)
				total_replies += len(t['replies'])
			user_data = {
				'profile': user_dict,
				'tweets': tweets_dicts
			}
			with open(cache_file, 'w', encoding='utf-8') as f:
				# noinspection PyTypeChecker
				json.dump(user_data, f, indent=4, default=lambda o: o.isoformat() if isinstance(o, dt) else o)
			print(f"Fetched {len(tweets_dicts)} tweets, {total_replies} replies for @{user_dict['username']}")
			await asyncio.sleep(2)
			return user_data
		except Exception as e:
			print(f"Error fetching data for @{user_dict['username']}: {str(e)}")
			return None


async def main(target_username, include_self):
	os.makedirs('input', exist_ok=True)
	os.makedirs('output', exist_ok=True)
	os.makedirs('output/users', exist_ok=True)

	api = API(pool='input/accounts.db')

	await api.pool.add_account("unused", "unused", "unused@example.com", "unused", cookies=get_cookies())
	await api.pool.login_all()

	print(f"Target user: @{target_username}")
	user = await api.user_by_login(target_username)
	print(f"User ID: {user.id}")

	cache_file_followers = f"output/{target_username}-followers.json"
	followers = await get_followers(api, user.id, cache_file_followers)
	print(f"Got {len(followers)} followers")

	cache_file_following = f"output/{target_username}-following.json"
	following = await get_following(api, user.id, cache_file_following)
	print(f"Got {len(following)} following")

	cache_file_follow_all = f"output/{target_username}-follow-all.json"
	follow_all = await get_follow_all(followers, following, user, cache_file_follow_all, include_self)
	print(f"Got {len(follow_all)} unique users in follow-all")

	print("Processing user data...")
	user_datas = []
	total_tweets = 0
	total_replies = 0
	skipped_users = 0
	for idx, user in enumerate(follow_all, 1):
		user_data = await get_user_data(api, user, cache_dir='output/users', fetch_limit=20, replies_limit=5)
		if user_data:
			user_datas.append(user_data)
			total_tweets += len(user_data['tweets'])
			total_replies += sum(len(t['replies']) for t in user_data['tweets'])
		else:
			skipped_users += 1
		print(f"Processed {idx}/{len(follow_all)} users")
	print(f"Summary: Processed {len(user_datas)} users, {skipped_users} skipped, {total_tweets} tweets, {total_replies} replies")


if __name__ == "__main__":
	asyncio.run(main(target_username="drxwilhelm", include_self=True))
