import asyncio
from twscrape import API, gather
import os
import json
from datetime import datetime as dt
from openai import AsyncOpenAI


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


async def aggregate_to_markdown(cache_dir, min_likes_posts, min_likes_replies):
	total_posts = 0
	total_replies = 0
	processed_users = 0
	skipped_users = 0
	for filename in os.listdir(cache_dir):
		if filename.endswith('.json'):
			json_path = os.path.join(cache_dir, filename)
			try:
				with open(json_path, 'r', encoding='utf-8') as f:
					data = json.load(f)
				if 'profile' not in data:
					print(f"Skipping invalid JSON file {filename}: missing 'profile' key")
					skipped_users += 1
					continue
				profile = data['profile']
				tweets = sorted(data['tweets'], key=lambda t: dt.fromisoformat(t['date']), reverse=True)  # Newest first
				processed_users += 1

				md_content = f"# Profile: @{profile['username']}\n"
				md_content += f"Bio: {profile['rawDescription']}\n"
				md_content += f"Location: {profile['location']}\n"
				md_content += f"Joined: {profile['created']}\n"
				md_content += f"Followers: {profile['followersCount']}\n"
				md_content += f"Following: {profile['friendsCount']}\n"
				md_content += f"Tweets: {profile['statusesCount']}\n"
				md_content += f"Likes: {profile['favouritesCount']}\n"
				md_content += f"Verified: {profile['verified']}\n"
				md_content += f"Blue: {profile['blue']}\n\n"

				included_posts = 0
				included_replies = 0
				for tweet in tweets:
					if tweet['likeCount'] >= min_likes_posts:
						included_posts += 1
						md_content += f"# Post by @{profile['username']} at {tweet['date']} (likes: {tweet['likeCount']}, retweets: {tweet['retweetCount']}, replies: {tweet['replyCount']}, views: {tweet['viewCount']})\n"
						md_content += f"{tweet['rawContent']}\n"
						if tweet['hashtags']:
							md_content += f"Hashtags: {', '.join(tweet['hashtags'])}\n"
						if tweet['mentionedUsers']:
							md_content += f"Mentions: {', '.join('@' + u['username'] for u in tweet['mentionedUsers'])}\n"
						if tweet['links']:
							md_content += f"Links: {', '.join(l['text'] for l in tweet['links'])}\n"
						if tweet.get('possibly_sensitive', False):
							md_content += "(Possibly sensitive)\n"
						md_content += "\n"
						for reply in tweet['replies']:
							if reply['likeCount'] >= min_likes_replies:
								included_replies += 1
								md_content += f"## Reply at {reply['date']} (likes: {reply['likeCount']}, retweets: {reply['retweetCount']}, views: {reply['viewCount']})\n"
								md_content += f"{reply['rawContent']}\n"
								if reply.get('possibly_sensitive', False):
									md_content += "(Possibly sensitive)\n"
								md_content += "\n"

				md_filename = filename.replace('.json', '.md')
				md_path = os.path.join(cache_dir, md_filename)
				with open(md_path, 'w', encoding='utf-8') as f:
					f.write(md_content)

				print(f"Aggregated @{profile['username']}: Included {included_posts} posts, {included_replies} replies")
				total_posts += included_posts
				total_replies += included_replies
			except Exception as e:
				print(f"Skipping invalid JSON file {filename}: {str(e)}")
				skipped_users += 1

	print(f"Aggregation complete: Processed {processed_users} users, {skipped_users} skipped, included {total_posts} posts, {total_replies} replies with min_likes_posts={min_likes_posts}, min_likes_replies={min_likes_replies}")


async def generate_avatars(cache_dir, avatar_dir):
	os.makedirs(avatar_dir, exist_ok=True)
	client = AsyncOpenAI(api_key=os.getenv('OPENAI_API_KEY'))
	processed_users = 0
	failed_users = 0
	for filename in os.listdir(cache_dir):
		if filename.endswith('.md'):
			processed_users += 1
			md_path = os.path.join(cache_dir, filename)
			with open(md_path, 'r', encoding='utf-8') as f:
				md_content = f.read()

			user_id = filename.split('-')[0]  # Extract user_id from filename (e.g., '12345-data.md')
			try:
				response = await client.chat.completions.create(
					model="gpt-4o",
					messages=[
						{"role": "system", "content": """
You are given a markdown file with a user's profile, posts, and replies. Create an audience avatar by analyzing the content objectively, emphasizing personality traits and content style. Output JSON with:
- "username": The user's username.
- "demographics": {"location": from profile, "bio_keywords": top 10 nouns/phrases from bio or all if fewer, "joined_year": year from joined date}.
- "personality": {"traits": 5-10 personality traits (e.g., curious, witty, analytical, passionate) based on posts/replies, "content_style": summary of post style (e.g., conversational, technical, poetic), "interaction_style": summary of reply behavior (e.g., supportive, debate-heavy, humorous)}.
- "interests": Top 20 topics/keywords from posts/replies, weighted by frequency and likes.
- "content_summary": {"posts": summary of all posts' content (themes, style), "replies": summary of all replies' content (themes, interactions), "hashtags": all unique hashtags, "mentions": all unique mentioned usernames}.
- "engagement": {"avg_likes": average likes across posts, "avg_retweets": average retweets, "avg_views": average views, "top_posts": list of top 3 posts by likes with rawContent, likes, and date}.
- "activity": {"post_count": number of posts, "reply_count": number of replies, "total_statuses": profile’s statusesCount}.
Input:
{markdown_content}
Output JSON only.
"""},
						{"role": "user", "content": md_content}
					],
					response_format={"type": "json_object"}
				)
				avatar = json.loads(response.choices[0].message.content)
				avatar_path = os.path.join(avatar_dir, f"{user_id}-avatar.json")
				with open(avatar_path, 'w', encoding='utf-8') as f:
					# noinspection PyTypeChecker
					json.dump(avatar, f, indent=4)
				print(f"Generated avatar for @{avatar['username']}")
			except Exception as e:
				print(f"Failed to generate avatar for user_id {user_id}: {str(e)}")
				failed_users += 1

	print(f"Avatar generation complete: Processed {processed_users} users, {failed_users} failed")


async def main(target_username, include_self):
	os.makedirs('input', exist_ok=True)
	os.makedirs('output', exist_ok=True)
	os.makedirs('output/users', exist_ok=True)
	os.makedirs('output/avatars', exist_ok=True)

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

	await aggregate_to_markdown('output/users', min_likes_posts=2, min_likes_replies=2)
	await generate_avatars('output/users', 'output/avatars')


if __name__ == "__main__":
	asyncio.run(main(target_username="drxwilhelm", include_self=True))
